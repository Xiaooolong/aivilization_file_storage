import requests
import jwt
import datetime

# ===== 配置区 =====
PRIVATE_KEY_PATH = "/Users/countingsheep/Projects/Unity/private.pem"   # 你的私钥路径
CHARACTER_ID = "693595"            # 测试用的角色 ID
CHARACTER_ID_FAKE = "999999"       # 用于测试 characterId 不匹配的 ID
# API_URL = f"http://127.0.0.1:8001/characters/{CHARACTER_ID_FAKE}/sas"  # 本地 FastAPI 服务地址

API_URL = f"https://api.aivilization.ai/fileStorage/characters/{CHARACTER_ID}/sas"  # 本地 FastAPI 服务地址

# ===== 生成 JWT token =====
with open(PRIVATE_KEY_PATH, "rb") as f:
    private_key = f.read()

payload = {
    "userId": "test-user-123",
    "characterId": 69359577,
    "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1),
    "iat": datetime.datetime.utcnow(),
    "iss": "Game Server",
    "aud": "user_id_here",
}

token = jwt.encode(payload, private_key, algorithm="RS256")
print("✅ 生成的 JWT Token:", token)
# ===== 调用 API =====
# headers = {
#     "Authentication": f"Bearer {token}"
# }

# resp = requests.get(API_URL, headers=headers)

# print("✅ 请求 URL:", API_URL)
# print("✅ 返回状态码:", resp.status_code)
# print("✅ 返回内容:", resp.text)
# https://aivilization-stage-report.onrender.com/?lang=en&char_id=69359577&token=eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOiJ0ZXN0LXVzZXItMTIzIiwiY2hhcmFjdGVySWQiOjY5MzU5NTc3LCJleHAiOjE3NTkzMjEyOTgsImlhdCI6MTc1OTMxNzY5OCwiaXNzIjoiR2FtZSBTZXJ2ZXIiLCJhdWQiOiJ1c2VyX2lkX2hlcmUifQ.CczFEFxJyaDNK3uyyjSZIZgS6wRXJHaSp37z_D07zHO7qjhDrEd-os5ucm_hATvM25VNMHFrqbhM_nm7R7eJVAyKFnsdA620f_W6CDcsywfKNLafVUHvxkcV5dfiu7Oy1Ju6NqMBHxXLygL6DavbiGQeYZVvBtW3E6b8gpvsSZLAnYIZgOCSjWaqt10LUkQSjMNLU6zntSt9f2qiZFhqqcA1oNSVP-GFgtj7A7wT05DlwIgQ8wuWBpkPsK7OFeIM1FNz5cnUfNv1x5lml_yakJteoLXTUZU6CNFFXaTUFfbp4UMauhbZL2qrU31RUt2NZTP4zOzUqB2yQncSrFiPUw