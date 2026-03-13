from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import firebase_admin
from firebase_admin import credentials, auth as firebase_auth
import asyncio, logging, os, json
import httpx  # Upstash REST client

# ------------------------
# App Init
# ------------------------
app = FastAPI()
logging.basicConfig(level=logging.INFO)

# ------------------------
# CORS
# ------------------------
origins = ["https://anshtechgears.netlify.app"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------
# Global Exception Handler
# ------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logging.error(f"Unhandled Error: {exc}")
    return JSONResponse(
        status_code=500,
        content={"message": f"Internal Server Error: {str(exc)}"},
    )

# ------------------------
# Firebase Setup
# ------------------------
cred_path = "serviceAccountKey.json"
if not os.path.exists(cred_path):
    raise Exception(f"{cred_path} not found!")
cred = credentials.Certificate(cred_path)
firebase_admin.initialize_app(cred)

# ------------------------
# Connection Manager
# ------------------------
class ConnectionManager:
    def __init__(self):
        self.connections = {}

    async def connect(self, user_id, websocket: WebSocket):
        await websocket.accept()
        self.connections[user_id] = websocket
        logging.info(f"User connected: {user_id}")

    def disconnect(self, user_id):
        if user_id in self.connections:
            del self.connections[user_id]
            logging.info(f"User disconnected: {user_id}")

    async def send(self, user_id, message):
        ws = self.connections.get(user_id)
        if ws:
            try:
                await ws.send_json(message)
            except Exception as e:
                logging.warning(f"Failed to send to {user_id}: {e}")

manager = ConnectionManager()

# ------------------------
# Connected Users API
# ------------------------
@app.get("/connected-users")
async def get_connected_users():
    return {"users": list(manager.connections.keys())}

# ------------------------
# Upstash Redis Setup
# ------------------------
UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")
CHANNEL = "notifications"

async def upstash_publish(channel, message):
    async with httpx.AsyncClient() as client:
        try:
            payload = {
                "cmd": "PUBLISH",
                "channel": channel,
                "message": json.dumps(message)
            }
            headers = {"Authorization": f"Bearer {UPSTASH_TOKEN}"}
            await client.post(UPSTASH_URL, json=payload, headers=headers)
        except Exception as e:
            logging.error(f"Upstash publish error: {e}")

# ------------------------
# WebSocket Endpoint
# ------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    token = websocket.query_params.get("token")
    try:
        decoded = firebase_auth.verify_id_token(token)
        user_id = decoded["uid"]
    except Exception as e:
        logging.warning(f"Invalid Firebase token: {e}")
        await websocket.close()
        return

    await manager.connect(user_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(user_id)

# ------------------------
# Notification API
# ------------------------
@app.post("/notify")
async def notify(data: dict = Body(...)):
    message = data.get("message", "").strip()
    user_id = data.get("user_id", "").strip()

    if not message:
        return {"status": "failed", "reason": "Message cannot be empty"}

    payload = {"type": "notification", "message": message}

    # ---- Broadcast to all Firebase users ----
    if user_id.upper() == "ALL" or user_id == "":
        try:
            page = firebase_auth.list_users()
            uids = [user.uid for user in page.users]
            for uid in uids:
                await manager.send(uid, {**payload, "user_id": uid})
                await upstash_publish(CHANNEL, {**payload, "user_id": uid})
            logging.info(f"Broadcast sent to {len(uids)} users")
        except Exception as e:
            logging.error(f"Failed to broadcast: {e}")
            return {"status": "failed", "reason": str(e)}
        return {"status": "broadcast_sent", "users": len(uids)}

    # ---- Single user ----
    else:
        await manager.send(user_id, {**payload, "user_id": user_id})
        await upstash_publish(CHANNEL, {**payload, "user_id": user_id})
        logging.info(f"Message sent to {user_id}")
        return {"status": "sent", "user_id": user_id}
