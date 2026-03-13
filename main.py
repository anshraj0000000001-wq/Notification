from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Body
import firebase_admin
from firebase_admin import credentials, auth
import redis
import json
import asyncio
import logging
import os
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi import Request

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"message": f"Internal Server Error: {str(exc)}"},
    )
origins = [
    "https://anshtechgears.netlify.app",  # tumhara frontend
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,     # sirf ye domain allow hoga
    allow_credentials=True,
    allow_methods=["*"],       # GET, POST, etc
    allow_headers=["*"],       # Content-Type, Authorization
)
# ------------------------
# App Init
# ------------------------

app = FastAPI()

logging.basicConfig(level=logging.INFO)

REDIS_URL = os.getenv("REDIS_URL","redis://localhost:6379")

redis_client = redis.Redis.from_url(REDIS_URL)

CHANNEL = "notifications"

# ------------------------
# Firebase Setup
# ------------------------

cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)

# ------------------------
# Connection Manager
# ------------------------

class ConnectionManager:

    def __init__(self):
        self.connections = {}

    async def connect(self,user_id,websocket:WebSocket):

        await websocket.accept()

        self.connections[user_id] = websocket

        logging.info(f"user connected {user_id}")

    def disconnect(self,user_id):

        if user_id in self.connections:

            del self.connections[user_id]

            logging.info(f"user disconnected {user_id}")

    async def send(self,user_id,message):

        ws = self.connections.get(user_id)

        if ws:

            await ws.send_json(message)

manager = ConnectionManager()

# ------------------------
# Redis Listener
# ------------------------

async def redis_listener():

    pubsub = redis_client.pubsub()

    pubsub.subscribe(CHANNEL)

    while True:

        message = pubsub.get_message()

        if message and message["type"] == "message":

            data = json.loads(message["data"])

            user_id = data["user_id"]

            await manager.send(user_id,data)

        await asyncio.sleep(0.01)

# ------------------------
# Startup Event
# ------------------------

@app.on_event("startup")
async def startup_event():

    asyncio.create_task(redis_listener())

# ------------------------
# WebSocket Endpoint
# ------------------------

@app.websocket("/ws")

async def websocket_endpoint(websocket:WebSocket):

    token = websocket.query_params.get("token")

    try:

        decoded = auth.verify_id_token(token)

        user_id = decoded["uid"]

    except:

        await websocket.close()

        return

    await manager.connect(user_id,websocket)

    try:

        while True:

            await websocket.receive_text()

    except WebSocketDisconnect:

        manager.disconnect(user_id)

# ------------------------
# Notification API
# ------------------------

@app.post("/notify")

async def notify(data:dict = Body(...)):

    payload = {

        "user_id":data["user_id"],

        "type":"notification",

        "message":data["message"]

    }

    redis_client.publish(CHANNEL,json.dumps(payload))

    return {"status":"sent"}
