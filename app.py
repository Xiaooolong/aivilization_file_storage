import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from urllib import request
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from loguru import logger
from dotenv import load_dotenv
import jwt
from jwt import ExpiredSignatureError, InvalidTokenError
from utils import JWTAuth

from azure.storage.blob import (
    generate_blob_sas,
    BlobSasPermissions,
)

load_dotenv(override=False)

CONNECTION_STRING = os.getenv("AZURE_BLOB_CONNECTION_STRING")
SAS_TTL_MIN = int(os.getenv("SAS_TTL_MIN", "5"))
JWT_PUBLIC_KEY_PATH = os.getenv("JWT_PUBLIC_KEY_PATH", "./public.pem")
try:
    with open(JWT_PUBLIC_KEY_PATH, "rb") as f:
        JWT_PUBLIC_KEY = f.read()
except Exception:
    JWT_PUBLIC_KEY = None
auth = JWTAuth(public_key=JWT_PUBLIC_KEY)

# 1) 把 .env 里的三个容器读出来
CONTAINER_MAP = {
    "cn": os.getenv("AZURE_BLOB_CONTAINER_CN", "reports-cn"),
    "hk": os.getenv("AZURE_BLOB_CONTAINER_HK", "reports-hk"),
    "en": os.getenv("AZURE_BLOB_CONTAINER_EN", "reports-en"),
}

# 2) 用启动时传入的 APP_LOCALE 选择默认容器（不传则默认 cn）
APP_LOCALE = os.getenv("APP_LOCALE", "cn").lower()
if APP_LOCALE not in CONTAINER_MAP:
    # 给个安全兜底，避免拼错导致 KeyError
    APP_LOCALE = "cn"

# 兼容：仍保留旧的 CONTAINER_NAME 变量，用于 build_sas_url 的默认值
CONTAINER_NAME = CONTAINER_MAP[APP_LOCALE]


# ----------------------------
# App & Logging setup
# ----------------------------
app = FastAPI(title="SAS Link Service", version="1.0.0")

# CORS (adjust origins as needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

LOG_DIR = os.getenv("LOG_DIR", "./logs")
os.makedirs(LOG_DIR, exist_ok=True)
logger.remove()  # remove default stderr sink
logger.add(
    os.path.join(LOG_DIR, "app_{time:YYYY-MM-DD}.log"),
    rotation="00:00",
    retention="14 days",
    enqueue=True,  # process-safe for multiple workers
    backtrace=False,
    diagnose=False,
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {process} | {message}",
)



class ApiResponse(BaseModel):
    code: int
    message: str
    data: Optional[Any] = None


def parse_conn_str(conn_str: str):
    parts = dict(p.split("=", 1) for p in conn_str.split(";") if "=" in p)
    return parts["AccountName"], parts["AccountKey"], parts.get("EndpointSuffix", "core.windows.net")


def resolve_blob_name(character_id: str) -> Optional[str]:
    """Map a character_id to a blob path.
    Replace this with a DB/query as needed. Here we assume <character_id>.pdf inside DEFAULT_CONTAINER.
    """
    # Example rule-based mapping
    return f"{character_id}.pdf"


async def build_sas_url(character_id: str, *, container_name: Optional[str] = None) -> str:
    account_name, account_key, endpoint_suffix = parse_conn_str(CONNECTION_STRING)
    container = container_name or CONTAINER_NAME

    blob_name = resolve_blob_name(character_id)
    if not blob_name:
        raise FileNotFoundError("Blob not found for given character_id")

    now = datetime.utcnow()
    start = now - timedelta(minutes=5)
    expiry = now + timedelta(minutes=SAS_TTL_MIN)

    sas = generate_blob_sas(
        account_name=account_name,
        container_name=container,
        blob_name=blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=expiry,
        start=start,
        protocol="https",
        content_disposition=f'attachment; filename="{quote(os.path.basename(blob_name))}"',
        content_type="application/pdf",
    )

    url = f"https://{account_name}.blob.{endpoint_suffix}/{container}/{blob_name}?{sas}"
    return url


@app.middleware("http")
async def log_requests(request: Request, call_next):
    try:
        client = request.client.host if request.client else "-"
        logger.info(f"REQ {request.method} {request.url.path} | client={client} | query={dict(request.query_params)}")
        response = await call_next(request)
        # Attempt to peek code/message if our shape
        try:
            body: bytes = b"".join([chunk async for chunk in response.body_iterator])  # type: ignore
            response.body_iterator = iter([body])  # reattach
            payload = body.decode("utf-8") if body else ""
            logger.info(f"RESP {request.method} {request.url.path} | status={response.status_code} | body={payload[:500]}")
        except Exception:
            logger.info(f"RESP {request.method} {request.url.path} | status={response.status_code}")
        return response
    except Exception as e:
        logger.exception(f"Unhandled error in middleware: {e}")
        return JSONResponse(status_code=500, content=ApiResponse(code=0, message="Internal Server Error").dict())


@app.get("/health", response_model=ApiResponse)
async def health() -> ApiResponse:
    return ApiResponse(code=1, message="OK", data={"version": app.version})


@app.get("/characters/{character_id}/sas", response_model=ApiResponse)
async def get_sas(
    request: Request,
    character_id: str,
    container: Optional[str] = None,
    locale: Optional[str] = None,
) -> ApiResponse:
    """
    Return a temporary SAS url for a given character_id.
    Query param `container` can override the default container.
    Query param `locale` can be one of: cn / hk / en.
    """
    try:
        _ = auth.verify_and_match(request, character_id)
    except ValueError as e:
        return JSONResponse(
            status_code=401,
            content=ApiResponse(code=0, message=str(e)).dict()
        )
        
    try:
        container_name = container
        if not container_name and locale:
            container_name = CONTAINER_MAP.get(locale.lower())

        url = await build_sas_url(character_id, container_name=container_name)
        return ApiResponse(code=1, message="Success", data=url)
    except FileNotFoundError as e:
        return ApiResponse(code=0, message=str(e))
    except Exception as e:
        logger.exception(f"SAS generation failed for character_id={character_id}: {e}")
        return ApiResponse(code=0, message="Failed to generate SAS link")



@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return JSONResponse(status_code=404, content=ApiResponse(code=0, message="Not Found").dict())


@app.exception_handler(500)
async def internal_error_handler(request: Request, exc):
    return JSONResponse(status_code=500, content=ApiResponse(code=0, message="Internal Server Error").dict())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=True,
        workers=1,
    )
