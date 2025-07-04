from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def read_root():
    retun{"message" : "Hello from VRChat notifier"} 