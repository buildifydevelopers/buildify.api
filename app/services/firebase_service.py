import logging
from typing import Any, Dict

try:
    from firebase_admin import credentials, firestore, initialize_app, get_app
except ImportError:
    firebase_admin = None
    credentials = None
    firestore = None
    initialize_app = None
    get_app = None

logger = logging.getLogger(__name__)


class FirebaseService:
    """Firebase Firestore service for saving payments and other data."""
    
    def __init__(self):
        self.db = None
        self._initialized = False
    
    def initialize(self):
        """Initialize Firebase Admin SDK."""
        try:
            if not firebase_admin or not credentials or not firestore:
                logger.warning("Firebase Admin SDK not installed. Install with: pip install firebase-admin")
                return False
            
            try:
                # Check if already initialized
                get_app()
                self.db = firestore.client()
                self._initialized = True
                logger.info("Firebase already initialized")
                return True
            except ValueError:
                # Not initialized yet
                # Support two deployment modes:
                # 1) FIREBASE_CREDENTIALS_JSON env var (recommended for serverless platforms)
                # 2) FIREBASE_CREDENTIALS_PATH file path (legacy)
                import os
                creds_json = os.getenv("FIREBASE_CREDENTIALS_JSON")
                creds_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "firebase-key.json")

                if creds_json:
                    # Write JSON to a temp file and initialize
                    try:
                        import tempfile
                        fd, tmp_path = tempfile.mkstemp(prefix="fb_creds_", suffix=".json")
                        with open(tmp_path, "w", encoding="utf-8") as f:
                            f.write(creds_json)
                        cred = credentials.Certificate(tmp_path)
                        initialize_app(cred)
                        self.db = firestore.client()
                        self._initialized = True
                        logger.info("Firebase initialized from FIREBASE_CREDENTIALS_JSON env var")
                        return True
                    except Exception as exc:
                        logger.error(f"Failed to init Firebase from FIREBASE_CREDENTIALS_JSON: {exc}")

                if os.path.exists(creds_path):
                    cred = credentials.Certificate(creds_path)
                    initialize_app(cred)
                    self.db = firestore.client()
                    self._initialized = True
                    logger.info("Firebase initialized with credentials file")
                    return True

                # Fallback to Application Default Credentials
                logger.warning(f"Firebase credentials not provided; attempting Application Default Credentials")
                initialize_app()
                self.db = firestore.client()
                self._initialized = True
                logger.info("Firebase initialized with Application Default Credentials")
                return True
        
        except Exception as exc:
            logger.error(f"Firebase initialization error: {exc}")
            self._initialized = False
            return False
    
    async def save_payment(self, payment_data: Dict[str, Any]) -> str:
        """
        Save payment data to Firestore.
        
        Args:
            payment_data: Dictionary with payment details
        
        Returns:
            Document ID of saved payment
        """
        try:
            if not self._initialized or not self.db:
                self.initialize()
            
            if not self.db:
                raise Exception("Firestore not initialized")
            
            # Add to payments collection
            doc_ref = self.db.collection("payments").add(payment_data)
            doc_id = doc_ref[1].id if isinstance(doc_ref, tuple) else str(doc_ref)
            
            logger.info(f"Payment saved to Firestore: {doc_id}")
            return doc_id
        
        except Exception as exc:
            logger.error(f"Error saving payment to Firestore: {exc}")
            raise
    
    async def get_payment(self, payment_id: str) -> Dict[str, Any] | None:
        """Get payment data from Firestore."""
        try:
            if not self.db:
                self.initialize()
            
            query = self.db.collection("payments").where("transactionId", "==", payment_id)
            docs = query.stream()
            
            for doc in docs:
                return doc.to_dict()
            
            return None
        
        except Exception as exc:
            logger.error(f"Error fetching payment from Firestore: {exc}")
            return None


# Global instance
firebase_service = FirebaseService()
