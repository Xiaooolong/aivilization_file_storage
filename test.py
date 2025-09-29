import requests
import jwt
import datetime

# ===== 配置区 =====
PRIVATE_KEY_PATH = r"C:\Users\xiaoo\Desktop\jwt-keys\private.pem"   # 你的私钥路径
CHARACTER_ID = "693595"            # 测试用的角色 ID
CHARACTER_ID_FAKE = "999999"       # 用于测试 characterId 不匹配的 ID
API_URL = f"http://127.0.0.1:8001/characters/{CHARACTER_ID_FAKE}/sas"  # 本地 FastAPI 服务地址

# ===== 生成 JWT token =====
with open(PRIVATE_KEY_PATH, "rb") as f:
    private_key = f.read()

payload = {
    "userId": "test-user-123",
    "characterId": CHARACTER_ID,
    "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1),
    "iat": datetime.datetime.utcnow(),
    "iss": "Game Server",
    "aud": "user_id_here",
}

token = jwt.encode(payload, private_key, algorithm="RS256")

# ===== 调用 API =====
headers = {
    "Authentication": f"Bearer {token}"
}

resp = requests.get(API_URL, headers=headers)

print("✅ 请求 URL:", API_URL)
print("✅ 返回状态码:", resp.status_code)
print("✅ 返回内容:", resp.text)
