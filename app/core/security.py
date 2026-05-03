from datetime import datetime, timedelta
from typing import Optional, Union, Any
from jose import jwt, JWTError
from passlib.context import CryptContext
from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Bcrypt has a 72 byte limit
MAX_PASSWORD_LENGTH = 72

def create_access_token(subject: Union[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """
    Create JWT access token
    """
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode = {"exp": expire, "sub": str(subject)}
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a password against a hash
    """
    # Truncate password if too long (bcrypt limitation)
    if len(plain_password.encode('utf-8')) > MAX_PASSWORD_LENGTH:
        plain_password = plain_password[:MAX_PASSWORD_LENGTH]
    
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except ValueError as e:
        # Handle any bcrypt errors gracefully
        print(f"Password verification error: {e}")
        return False

def get_password_hash(password: str) -> str:
    """
    Hash a password
    """
    # Truncate password if too long (bcrypt limitation)
    if len(password.encode('utf-8')) > MAX_PASSWORD_LENGTH:
        password = password[:MAX_PASSWORD_LENGTH]
    
    return pwd_context.hash(password)

def decode_token(token: str) -> Optional[str]:
    """
    Decode JWT token and return subject (user_id)
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: str = payload.get("sub")
        return user_id
    except JWTError:
        return None