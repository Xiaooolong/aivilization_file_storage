import os
from typing import Optional, Dict, Any
from fastapi import Request
from loguru import logger
from dotenv import load_dotenv
import jwt


class JWTAuth:
    def __init__(self, public_key: Optional[str]):
        self.public_key = public_key

    def _extract_bearer_token(self, request: Request) -> Optional[str]:
        """
        从请求头提取 JWT：
          - Authentication: <token> 或 Bearer <token>
          - Authorization:  Bearer <token>
        """
        header = request.headers.get("Authentication")
        if header:
            parts = header.strip().split()
            if len(parts) == 1:
                return parts[0]
            if len(parts) == 2 and parts[0].lower() == "bearer":
                return parts[1]

        return None

    def verify_and_match(self, request: Request, character_id: str) -> Dict[str, Any]:
        """
        校验 JWT：
          1. 不得过期
          2. token 中的 characterId 必须与 path 参数一致
          3. 解析过程中任何异常 → 拒绝
        """
        if not self.public_key:
            logger.error("JWT public key not configured")
            raise ValueError("JWT verification not available")

        token = self._extract_bearer_token(request)
        if not token:
            raise ValueError("Missing JWT")

        try:
            claims = jwt.decode(
                token,
                self.public_key,
                algorithms=["RS256"],
                options={"require": ["exp", "iat"], "verify_exp": True, "verify_aud": False},
            )
        # except jwt.ExpiredSignatureError:
        #     raise ValueError("Token expired")
        # except jwt.InvalidTokenError:
        #     raise ValueError("Invalid token")
        # except Exception:
        #     raise ValueError("Invalid token")
        except Exception as e:
            logger.error(f"JWT decode error: {e}")
            raise ValueError("Invalid token.")

        if str(claims.get("characterId")) != str(character_id):
            logger.error(f"JWT decode error: {claims.get('characterId')} != {character_id} mismatch.")
            raise ValueError("Invalid token.")

        return claims
