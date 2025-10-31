import firebase_admin
from firebase_admin import credentials, db
import json, os

def initialize_firebase():
    try:
        if not firebase_admin._apps:
            # 🔥 Secret файл немесе environment-тен оқу
            firebase_secret = os.environ.get("FIREBASE_SECRET")
            if firebase_secret:
                cred_data = json.loads(firebase_secret)
                cred = credentials.Certificate(cred_data)
            elif os.path.exists("firebase_secret.json"):
                cred = credentials.Certificate("firebase_secret.json")
            else:
                print("🚫 Firebase secret табылмады!")
                return None, None

            firebase_admin.initialize_app(cred, {
                "databaseURL": "https://kinobot-fe2ac-default-rtdb.firebaseio.com/"
            })
        ref_root = db.reference("/")
        return ref_root.child("bots"), ref_root.child("users")
    except Exception as e:
        print("🚫 Firebase қатесі:", e)
        return None, None
