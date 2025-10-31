import os
import json
import firebase_admin
from firebase_admin import credentials, db

def initialize_firebase():
    try:
        print("🔄 Firebase байланысын тексеру...")

        firebase_json = os.getenv("FIREBASE_SECRET")
        if not firebase_json:
            print("🚫 Firebase secret табылмады!")
            return None, None

        creds_dict = json.loads(firebase_json)
        cred = credentials.Certificate(creds_dict)

        # 🔥 Өз database URL-ыңды жаз:
        firebase_admin.initialize_app(cred, {
            "databaseURL": "https://manybot-kz-default-rtdb.firebaseio.com/"
        })

        print("✅ Firebase сәтті қосылды!")
        users_ref = db.reference("users")
        memory_ref = db.reference("memory")
        return users_ref, memory_ref

    except Exception as e:
        print(f"🚫 Firebase қатесі: {e}")
        return None, None
