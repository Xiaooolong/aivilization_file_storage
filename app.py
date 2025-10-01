import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from urllib import request
from urllib.parse import quote
from starlette.concurrency import iterate_in_threadpool

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
    BlobServiceClient
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

BLOB_SERVICE = BlobServiceClient.from_connection_string(CONNECTION_STRING)

# 1) 把 .env 里的三个容器读出来
CONTAINER_MAP = {
    "cn": os.getenv("AZURE_BLOB_CONTAINER_CN", "reports-cn"),
    "hk": os.getenv("AZURE_BLOB_CONTAINER_HK", "reports-hk"),
    "en": os.getenv("AZURE_BLOB_CONTAINER_EN", "reports-en"),
}

# 证书容器：certificates-<region>
CERT_CONTAINER_MAP = {
    "cn": os.getenv("AZURE_BLOB_CERT_CONTAINER_CN", "certificates-cn"),
    "hk": os.getenv("AZURE_BLOB_CERT_CONTAINER_HK", "certificates-hk"),
    "en": os.getenv("AZURE_BLOB_CERT_CONTAINER_EN", "certificates-en"),
}


# 2) 用启动时传入的 APP_REGION 选择默认容器（不传则默认 cn）
APP_REGION = os.getenv("APP_REGION", "cn").lower()
if APP_REGION not in CONTAINER_MAP:
    # 给个安全兜底，避免拼错导致 KeyError
    APP_REGION = "cn"

# 兼容：仍保留旧的 CONTAINER_NAME 变量，用于 build_sas_url 的默认值
CONTAINER_NAME = CONTAINER_MAP[APP_REGION]
CERT_CONTAINER_NAME = CERT_CONTAINER_MAP[APP_REGION]



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

def blob_exists(container_name: str, blob_name: str) -> bool:
    try:
        bc = BLOB_SERVICE.get_blob_client(container=container_name, blob=blob_name)
        return bc.exists()  # SDK 自带 exists()
    except Exception:
        return False

async def build_sas_url(
    character_id: str,
    *,
    container_name: Optional[str] = None,
    view: str = "attachment",                # "inline" 或 "attachment"
    filename: Optional[str] = None,          # 覆盖文件名（可选）
    content_type: str = "application/pdf",   # 默认 PDF
    blob_name_override: Optional[str] = None # ✅ 新增：可自定义 blob 名
) -> str:

    account_name, account_key, endpoint_suffix = parse_conn_str(CONNECTION_STRING)
    container = container_name or CONTAINER_NAME

    blob_name = blob_name_override or resolve_blob_name(character_id)
    if not blob_name:
        raise FileNotFoundError("Blob not found for given character_id")

    # 生成 Content-Disposition
    # - inline：浏览器内嵌预览
    # - attachment：浏览器下载
    disp = "inline" if view.lower() == "inline" else "attachment"

    # 文件名（默认取 blob basename）
    basename = filename or os.path.basename(blob_name)

    # 注意：这里 filename 放在 header 里需要做 URL 编码，
    # 以避免空格/中文/特殊字符导致的 header 格式问题。
    encoded_filename = quote(basename)

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
        # 下面两个参数会在 SAS 中产生 rscd / rsct，
        # 且在响应时覆盖 Content-Disposition / Content-Type。
        content_disposition=f'{disp}; filename="{encoded_filename}"',
        content_type=content_type,
    )

    url = f"https://{account_name}.blob.{endpoint_suffix}/{container}/{blob_name}?{sas}"
    logger.info(f"build_sas_url: character_id={character_id}, view={view}, container={container_name}")
    from urllib.parse import quote as urlquote
    ascii_fallback = basename.encode('ascii', 'ignore').decode('ascii') or 'report.pdf'
    # content_disposition = f'{disp}; filename="{ascii_fallback}"; filename*=UTF-8\'\'{urlquote(basename, safe="")}'
    content_disposition = f'{disp}; filename="{ascii_fallback}"; filename*=UTF-8\'\'{urlquote(basename, safe="")}'
    logger.info(f"SAS override headers going to Azure: Content-Disposition='{content_disposition}', Content-Type='{content_type}'")
    return url



@app.middleware("http")
async def log_requests(request: Request, call_next):
    try:
        client = request.client.host if request.client else "-"
        logger.info(f"REQ {request.method} {request.url.path} | client={client} | query={dict(request.query_params)}")

        response = await call_next(request)

        try:
            body = b""
            async for chunk in response.body_iterator:
                body += chunk

            payload = body.decode("utf-8", "ignore") if body else ""
            logger.info(
                f"RESP {request.method} {request.url.path} | status={response.status_code} | body={payload[:500]}"
            )
            response.body_iterator = iterate_in_threadpool(iter([body]))
        except Exception:
            logger.info(f"RESP {request.method} {request.url.path} | status={response.status_code}")

        return response

    except Exception as e:
        logger.exception(f"Unhandled error in middleware: {e}")
        return JSONResponse(status_code=500, content=ApiResponse(code=0, message="Internal Server Error").dict())


@app.get("/health", response_model=ApiResponse)
async def health() -> ApiResponse:
    return ApiResponse(code=1, message="OK", data={"version": app.version})


@app.get("/sas/report/{character_id}", response_model=ApiResponse)
async def get_report_sas(
    request: Request,
    character_id: str,
    container: Optional[str] = None,
    region: Optional[str] = None,
    view: Optional[str] = None,            # 新增：inline / attachment
    filename: Optional[str] = None,        # 新增：自定义文件名（可选）
    content_type: Optional[str] = None,    # 新增：覆盖 content-type（可选）
) -> ApiResponse:
    """
    Return a temporary SAS url for a given character_id.
    Query param:
      - container: 覆盖容器名
      - region:    cn / hk / en （与 CONTAINER_MAP 关联）
      - view:      inline / attachment（默认保持原先语义：attachment）
      - filename:  自定义下载/预览显示的文件名（可选）
      - content_type: 默认 application/pdf
    """
    # logger.info(f"get_sas view={view!r} -> view_mode={view_mode!r}, container_name={container_name!r}")

    try:
        _ = auth.verify_and_match(request, character_id)
    except ValueError as e:
        print(f"Auth failed: {e}")
        return JSONResponse(
            status_code=401,
            content=ApiResponse(code=0, message=str(e)).dict()
        )

    try:
        container_name = container
        if not container_name and region:
            container_name = CONTAINER_MAP.get(region.lower())
        if not container_name:
            container_name = CONTAINER_NAME

        # 默认与原实现一致：不传 view 时按 attachment 生成（避免行为突变）
        view_mode = (view or "attachment").lower()
        if view_mode not in ("inline", "attachment"):
            view_mode = "attachment"

        logger.info(f"get_sas resolved -> view_mode={view_mode!r}, container_name={container_name!r}, filename={filename!r}")

        blob_name = resolve_blob_name(character_id)
        if not blob_exists(container_name, blob_name):
            return JSONResponse(
                status_code=404,
                content=ApiResponse(code=0, message="Not Found").dict()
            )

        url = await build_sas_url(
            character_id,
            container_name=container_name,
            view=view_mode,
            filename=filename,
            content_type=content_type or "application/pdf",
        )
        return ApiResponse(code=1, message="Success", data=url)
    except FileNotFoundError as e:
        return ApiResponse(code=0, message=str(e))
    except Exception as e:
        logger.exception(f"SAS generation failed for character_id={character_id}: {e}")
        return ApiResponse(code=0, message="Failed to generate SAS link")


@app.get("/sas/certificate/{character_id}", response_model=ApiResponse)
async def get_certificate_sas(
    request: Request,
    character_id: str,
    container: Optional[str] = None,
    region: Optional[str] = None,
    view: Optional[str] = None,            # inline / attachment
    filename: Optional[str] = None,        # 自定义下载/预览显示的文件名（可选）
) -> ApiResponse:
    """
    Return a temporary SAS url for the PNG certificate of a given character_id.

    Query param:
      - container: 覆盖容器名（不传则按 region / 默认）
      - region:    cn / hk / en （与 CERT_CONTAINER_MAP 关联）
      - view:      inline / attachment（默认 attachment）
      - filename:  自定义文件名（可选）
    """
    try:
        _ = auth.verify_and_match(request, character_id)
    except ValueError as e:
        print(f"Auth failed: {e}")
        return JSONResponse(
            status_code=401,
            content=ApiResponse(code=0, message=str(e)).dict()
        )

    try:
        # 选择容器：优先 container 参数，其次 region 对应的证书容器，最后用默认证书容器
        container_name = container
        if not container_name and region:
            container_name = CERT_CONTAINER_MAP.get(region.lower())
        if not container_name:
            container_name = CERT_CONTAINER_MAP.get(APP_REGION, "certificates-cn")
        if not container_name:
            container_name = CERT_CONTAINER_NAME

        # 视图模式：默认 attachment
        view_mode = (view or "attachment").lower()
        if view_mode not in ("inline", "attachment"):
            view_mode = "attachment"

        logger.info(f"get_certificate_sas -> view_mode={view_mode!r}, container_name={container_name!r}, filename={filename!r}")

        # 证书固定是 PNG：blob 名形如 <char_id>.png
        png_blob_name = f"{character_id}.png"

        if not blob_exists(container_name, png_blob_name):
            return JSONResponse(
                status_code=404,
                content=ApiResponse(code=0, message="Not Found").dict()
            )

        url = await build_sas_url(
            character_id,
            container_name=container_name,
            view=view_mode,
            filename=filename or png_blob_name,   # 下载/预览时显示的文件名
            content_type="image/png",             # ✅ PNG
            blob_name_override=png_blob_name      # ✅ 指定证书的 blob 路径
        )
        return ApiResponse(code=1, message="Success", data=url)

    except FileNotFoundError as e:
        return ApiResponse(code=0, message=str(e))
    except Exception as e:
        logger.exception(f"Certificate SAS generation failed for character_id={character_id}: {e}")
        return ApiResponse(code=0, message="Failed to generate certificate SAS link")

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
        port=int(os.getenv("PORT", "8001")),
        reload=True,
        workers=1,
    )
