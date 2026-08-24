import os
import hmac
import hashlib
import base64
import secrets
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date,datetime,timedelta
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv
from authlib.integrations.starlette_client import OAuth,OAuthError
from starlette.config import Config
from starlette.middleware.sessions import SessionMiddleware
from fastapi import FastAPI,Depends,HTTPException,Header,UploadFile,File,Request
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel,Field
from sqlalchemy import Boolean,Date,DateTime,ForeignKey,Integer,String,Text,create_engine,func,or_
from sqlalchemy.orm import DeclarativeBase,Mapped,mapped_column,Session,sessionmaker

load_dotenv()

DATABASE_URL=os.getenv("DATABASE_URL")
SECRET_KEY=os.getenv("SECRET_KEY","developer-diary-development-secret")
APP_ENV=os.getenv("APP_ENV","production").strip().lower()

# Cloudflare Pages frontend.
# Keep FRONTEND_URL in Render environment variables so it can be changed
# without modifying the source code.
FRONTEND_URL=os.getenv(
    "FRONTEND_URL",
    "https://developer-diary.pages.dev"
).rstrip("/")

# Explicit Google OAuth callback for Render.
# Recommended Render value:
# https://developer-diary.onrender.com/api/auth/google/callback
GOOGLE_REDIRECT_URI=os.getenv("GOOGLE_REDIRECT_URI","").strip()

# Supabase Storage configuration.
# Keep the service-role key ONLY in Render/local backend environment variables.
SUPABASE_URL=os.getenv("SUPABASE_URL","").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY=os.getenv("SUPABASE_SERVICE_ROLE_KEY","").strip()
SUPABASE_STORAGE_BUCKET=os.getenv(
    "SUPABASE_STORAGE_BUCKET",
    "developer-diary-attachments"
).strip()

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing from environment variables")

if not SECRET_KEY or SECRET_KEY=="developer-diary-development-secret":
    if APP_ENV=="production":
        raise RuntimeError("SECRET_KEY must be set to a strong value in production")

engine=create_engine(DATABASE_URL,pool_pre_ping=True)
SessionLocal=sessionmaker(bind=engine,autoflush=False,autocommit=False)

config=Config(".env")
oauth=OAuth(config)

oauth.register(
    name="google",
    client_id=config("GOOGLE_CLIENT_ID"),
    client_secret=config("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope":"openid email profile"}
)

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__="users"
    id:Mapped[int]=mapped_column(Integer,primary_key=True,index=True)
    name:Mapped[str]=mapped_column(String(100),nullable=False)
    email:Mapped[str]=mapped_column(String(255),nullable=False,unique=True,index=True)
    password_hash:Mapped[str|None]=mapped_column(Text,nullable=True)
    google_id:Mapped[str|None]=mapped_column(String(255),nullable=True,unique=True)
    is_admin:Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)
    is_active:Mapped[bool]=mapped_column(Boolean,nullable=False,default=True)
    created_at:Mapped[datetime]=mapped_column(DateTime,nullable=False,server_default=func.now())
    updated_at:Mapped[datetime]=mapped_column(DateTime,nullable=False,server_default=func.now(),onupdate=func.now())

class Note(Base):
    __tablename__="notes"
    id:Mapped[int]=mapped_column(Integer,primary_key=True,index=True)
    user_id:Mapped[int]=mapped_column(ForeignKey("users.id",ondelete="CASCADE"),nullable=False,index=True)
    note_date:Mapped[date]=mapped_column(Date,nullable=False,index=True)
    title:Mapped[str]=mapped_column(String(200),nullable=False)
    category:Mapped[str]=mapped_column(String(30),nullable=False,default="GENERAL")
    content:Mapped[str]=mapped_column(Text,nullable=False)
    tags:Mapped[str|None]=mapped_column(Text,nullable=True)
    is_pinned:Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)
    is_archived:Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)
    created_at:Mapped[datetime]=mapped_column(DateTime,nullable=False,server_default=func.now())
    updated_at:Mapped[datetime]=mapped_column(DateTime,nullable=False,server_default=func.now(),onupdate=func.now())

class Task(Base):
    __tablename__="tasks"
    id:Mapped[int]=mapped_column(Integer,primary_key=True,index=True)
    user_id:Mapped[int]=mapped_column(ForeignKey("users.id",ondelete="CASCADE"),nullable=False,index=True)
    task_date:Mapped[date]=mapped_column(Date,nullable=False,index=True)
    title:Mapped[str]=mapped_column(String(500),nullable=False)
    description:Mapped[str|None]=mapped_column(Text,nullable=True)
    completed:Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)
    created_at:Mapped[datetime]=mapped_column(DateTime,nullable=False,server_default=func.now())
    updated_at:Mapped[datetime]=mapped_column(DateTime,nullable=False,server_default=func.now(),onupdate=func.now())

class NoteVersion(Base):
    __tablename__="note_versions"
    id:Mapped[int]=mapped_column(Integer,primary_key=True,index=True)
    note_id:Mapped[int]=mapped_column(ForeignKey("notes.id",ondelete="CASCADE"),nullable=False,index=True)
    title:Mapped[str]=mapped_column(String(200),nullable=False)
    category:Mapped[str]=mapped_column(String(30),nullable=False)
    content:Mapped[str]=mapped_column(Text,nullable=False)
    tags:Mapped[str|None]=mapped_column(Text,nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime,nullable=False,server_default=func.now())

class Attachment(Base):
    __tablename__="attachments"
    id:Mapped[int]=mapped_column(Integer,primary_key=True,index=True)
    note_id:Mapped[int]=mapped_column(ForeignKey("notes.id",ondelete="CASCADE"),nullable=False,index=True)
    file_name:Mapped[str]=mapped_column(String(255),nullable=False)
    file_path:Mapped[str]=mapped_column(Text,nullable=False)
    file_type:Mapped[str|None]=mapped_column(String(100),nullable=True)
    file_size:Mapped[int|None]=mapped_column(Integer,nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime,nullable=False,server_default=func.now())

class RegisterRequest(BaseModel):
    name:str=Field(...,min_length=2,max_length=100)
    email:str=Field(...,min_length=5,max_length=255)
    password:str=Field(...,min_length=6,max_length=200)

class LoginRequest(BaseModel):
    email:str
    password:str

class NoteCreate(BaseModel):
    note_date:date
    title:str=Field(...,min_length=1,max_length=200)
    category:str="GENERAL"
    content:str=Field(...,min_length=1)
    tags:list[str]=Field(default_factory=list)
    is_pinned:bool=False

class NoteUpdate(BaseModel):
    note_date:date|None=None
    title:str|None=Field(None,min_length=1,max_length=200)
    category:str|None=None
    content:str|None=Field(None,min_length=1)
    tags:list[str]|None=None
    is_pinned:bool|None=None
    is_archived:bool|None=None

class TaskCreate(BaseModel):
    task_date:date
    title:str=Field(...,min_length=1,max_length=500)
    description:str|None=Field(None,max_length=5000)

class TaskUpdate(BaseModel):
    task_date:date|None=None
    title:str|None=Field(None,min_length=1,max_length=500)
    description:str|None=Field(None,max_length=5000)
    completed:bool|None=None

class AdminStatusRequest(BaseModel):
    is_active:bool

def get_db():
    db=SessionLocal()
    try:
        yield db
    finally:
        db.close()

def hash_password(password:str)->str:
    salt=secrets.token_bytes(16)
    password_hash=hashlib.pbkdf2_hmac("sha256",password.encode(),salt,310000)
    return base64.urlsafe_b64encode(salt).decode()+"$"+base64.urlsafe_b64encode(password_hash).decode()

def verify_password(password:str,stored_hash:str)->bool:
    try:
        salt_string,hash_string=stored_hash.split("$",1)
        salt=base64.urlsafe_b64decode(salt_string.encode())
        expected=base64.urlsafe_b64decode(hash_string.encode())
        actual=hashlib.pbkdf2_hmac("sha256",password.encode(),salt,310000)
        return hmac.compare_digest(actual,expected)
    except Exception:
        return False

def create_token(user:User)->str:
    expires=int((datetime.utcnow()+timedelta(days=7)).timestamp())
    payload=f"{user.id}:{expires}"
    signature=hmac.new(SECRET_KEY.encode(),payload.encode(),hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode()

def get_current_user(authorization:str|None=Header(default=None),db:Session=Depends(get_db)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401,detail="Authentication required")

    token=authorization[7:]

    try:
        decoded=base64.urlsafe_b64decode(token.encode()).decode()
        user_id,expires,signature=decoded.split(":",2)
        payload=f"{user_id}:{expires}"
        expected=hmac.new(SECRET_KEY.encode(),payload.encode(),hashlib.sha256).hexdigest()

        if not hmac.compare_digest(signature,expected):
            raise HTTPException(status_code=401,detail="Invalid authentication token")

        if int(expires)<int(datetime.utcnow().timestamp()):
            raise HTTPException(status_code=401,detail="Authentication token expired")

        user=db.query(User).filter(User.id==int(user_id)).first()

        if not user:
            raise HTTPException(status_code=401,detail="User not found")

        if not user.is_active:
            raise HTTPException(status_code=403,detail="User account is disabled")

        return user

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401,detail="Invalid authentication token")

def get_admin_user(user:User=Depends(get_current_user)):
    if not user.is_admin:
        raise HTTPException(status_code=403,detail="Administrator access required")
    return user

def user_response(user:User):
    return {
        "id":user.id,
        "name":user.name,
        "email":user.email,
        "isAdmin":user.is_admin,
        "isActive":user.is_active,
        "createdAt":user.created_at
    }

def note_response(note:Note):
    return {
        "id":note.id,
        "date":note.note_date,
        "title":note.title,
        "category":note.category,
        "content":note.content,
        "tags":[tag.strip() for tag in (note.tags or "").split(",") if tag.strip()],
        "isPinned":note.is_pinned,
        "isArchived":note.is_archived,
        "createdAt":note.created_at,
        "updatedAt":note.updated_at
    }

def task_response(task:Task):
    return {
        "id":task.id,
        "date":task.task_date,
        "title":task.title,
        "description":task.description or "",
        "completed":task.completed,
        "createdAt":task.created_at,
        "updatedAt":task.updated_at
    }

def attachment_response(attachment:Attachment):
    url=None

    try:
        url=storage_signed_url(attachment.file_path)
    except Exception as error:
        print("Attachment signed URL error:",error)

    return {
        "id":attachment.id,
        "noteId":attachment.note_id,
        "fileName":attachment.file_name,
        "fileType":attachment.file_type,
        "fileSize":attachment.file_size,
        "createdAt":attachment.created_at,
        "url":url
    }


def _storage_headers(content_type:str|None=None):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured "
            "for attachment storage"
        )

    headers={
        "Authorization":f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "apikey":SUPABASE_SERVICE_ROLE_KEY,
    }

    if content_type:
        headers["Content-Type"]=content_type

    return headers


def _storage_request(method:str,path:str,body:bytes|None=None,content_type:str|None=None):
    request=urllib.request.Request(
        f"{SUPABASE_URL}/storage/v1/{path.lstrip('/')}",
        data=body,
        headers=_storage_headers(content_type),
        method=method,
    )

    try:
        with urllib.request.urlopen(request,timeout=60) as response:
            return response.status,response.read()
    except urllib.error.HTTPError as error:
        detail=error.read().decode("utf-8","replace")
        raise RuntimeError(
            f"Supabase Storage request failed ({error.code}): {detail}"
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError(
            f"Could not reach Supabase Storage: {error.reason}"
        ) from error


def ensure_storage_bucket():
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print(
            "WARNING: Supabase Storage is not configured. "
            "Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY."
        )
        return

    bucket_id=urllib.parse.quote(SUPABASE_STORAGE_BUCKET,safe="")

    try:
        _storage_request("GET",f"bucket/{bucket_id}")
        return
    except RuntimeError as error:
        if "404" not in str(error):
            print("Supabase Storage bucket check failed:",error)
            return

    payload=json.dumps({
        "id":SUPABASE_STORAGE_BUCKET,
        "name":SUPABASE_STORAGE_BUCKET,
        "public":False,
    }).encode("utf-8")

    try:
        _storage_request("POST","bucket/",payload,"application/json")
        print(f"Created Supabase Storage bucket: {SUPABASE_STORAGE_BUCKET}")
    except RuntimeError as error:
        if "already exists" not in str(error).lower():
            print("Supabase Storage bucket creation failed:",error)


def _encoded_storage_path(object_path:str):
    return "/".join(
        urllib.parse.quote(part,safe="")
        for part in object_path.split("/")
    )


def storage_upload(object_path:str,content:bytes,content_type:str|None):
    bucket=urllib.parse.quote(SUPABASE_STORAGE_BUCKET,safe="")
    _storage_request(
        "POST",
        f"object/{bucket}/{_encoded_storage_path(object_path)}",
        content,
        content_type or "application/octet-stream",
    )


def storage_delete(object_path:str):
    bucket=urllib.parse.quote(SUPABASE_STORAGE_BUCKET,safe="")
    _storage_request(
        "DELETE",
        f"object/{bucket}/{_encoded_storage_path(object_path)}"
    )


def storage_signed_url(object_path:str,expires_in:int=3600)->str:
    bucket=urllib.parse.quote(SUPABASE_STORAGE_BUCKET,safe="")
    payload=json.dumps({"expiresIn":expires_in}).encode("utf-8")

    _,raw=_storage_request(
        "POST",
        f"object/sign/{bucket}/{_encoded_storage_path(object_path)}",
        payload,
        "application/json",
    )

    data=json.loads(raw.decode("utf-8"))
    signed=data.get("signedURL") or data.get("signedUrl")

    if not signed:
        raise RuntimeError(f"Supabase Storage did not return a signed URL: {data}")

    return signed if signed.startswith("http") else f"{SUPABASE_URL}/storage/v1{signed}"


def migrate_database_schema():
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS description TEXT"
        )


@asynccontextmanager
async def lifespan(app:FastAPI):
    Base.metadata.create_all(bind=engine)
    migrate_database_schema()
    ensure_storage_bucket()
    yield

app=FastAPI(title="Developer Diary API",version="2.0.0",lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    max_age=600,
    same_site="lax",
    https_only=(APP_ENV=="production")
)

configured_origins=os.getenv("CORS_ORIGINS","").strip()

if configured_origins:
    ALLOWED_ORIGINS=[
        origin.strip().rstrip("/")
        for origin in configured_origins.split(",")
        if origin.strip()
    ]
else:
    ALLOWED_ORIGINS=[
        FRONTEND_URL,
        "http://127.0.0.1:5500",
        "http://localhost:5500"
    ]

# Remove duplicates while preserving order.
ALLOWED_ORIGINS=list(dict.fromkeys(ALLOWED_ORIGINS))

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

@app.get("/")
def root():
    return {
        "message":"Developer Diary API is running",
        "version":"2.0.0",
        "environment":APP_ENV
    }

@app.get("/api/health")
def health():
    try:
        with engine.connect() as connection:
            connection.execute(func.current_date())
        return {"status":"ok","database":"connected"}
    except Exception as error:
        return {"status":"error","database":"disconnected","detail":str(error)}

@app.post("/api/auth/register",status_code=201)
def register(data:RegisterRequest,db:Session=Depends(get_db)):
    email=data.email.strip().lower()

    existing=db.query(User).filter(User.email==email).first()

    if existing:
        raise HTTPException(status_code=409,detail="Email already registered")

    user=User(
        name=data.name.strip(),
        email=email,
        password_hash=hash_password(data.password),
        is_admin=False,
        is_active=True
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    token=create_token(user)

    return {
        "message":"Registration successful",
        "token":token,
        "user":user_response(user)
    }

@app.post("/api/auth/login")
def login(data:LoginRequest,db:Session=Depends(get_db)):
    email=data.email.strip().lower()
    user=db.query(User).filter(User.email==email).first()

    if not user or not user.password_hash or not verify_password(data.password,user.password_hash):
        raise HTTPException(status_code=401,detail="Invalid email or password")

    if not user.is_active:
        raise HTTPException(status_code=403,detail="User account is disabled")

    token=create_token(user)

    return {
        "message":"Login successful",
        "token":token,
        "user":user_response(user)
    }

@app.get("/api/auth/me")
def me(user:User=Depends(get_current_user)):
    return user_response(user)

@app.get("/api/users/me")
def get_profile(user:User=Depends(get_current_user)):
    return user_response(user)

@app.get("/api/auth/google")
async def google_login(request:Request):
    redirect_uri=GOOGLE_REDIRECT_URI or str(request.url_for("google_callback"))
    return await oauth.google.authorize_redirect(request,redirect_uri)

@app.get("/api/auth/google/callback",name="google_callback")
async def google_callback(request:Request,db:Session=Depends(get_db)):
    try:
        token_data=await oauth.google.authorize_access_token(request)

        user_info=token_data.get("userinfo")

        if not user_info:
            try:
                user_info=await oauth.google.userinfo(token=token_data)
            except Exception:
                user_info=None

        if not user_info:
            return RedirectResponse(f"{FRONTEND_URL}/?google_error=missing_user_info")

        google_id=user_info.get("sub")
        email=user_info.get("email")
        name=user_info.get("name") or user_info.get("given_name") or (email.split("@")[0] if email else "Developer")

        if not google_id or not email:
            return RedirectResponse(f"{FRONTEND_URL}/?google_error=missing_google_data")

        email=email.strip().lower()

        user=db.query(User).filter(User.google_id==google_id).first()

        if not user:
            user=db.query(User).filter(User.email==email).first()

        if user:
            if not user.is_active:
                return RedirectResponse(f"{FRONTEND_URL}/?google_error=account_disabled")

            if not user.google_id:
                user.google_id=google_id

            if not user.name.strip():
                user.name=name

            db.commit()
            db.refresh(user)

        else:
            user=User(
                name=name.strip(),
                email=email,
                password_hash=None,
                google_id=google_id,
                is_admin=False,
                is_active=True
            )

            db.add(user)
            db.commit()
            db.refresh(user)

        application_token=create_token(user)

        return RedirectResponse(
            f"{FRONTEND_URL}/?google_token={application_token}"
        )

    except OAuthError as error:
        print("Google OAuth error:",error)
        return RedirectResponse(f"{FRONTEND_URL}/?google_error=oauth_failed")

    except Exception as error:
        print("Google login error:",error)
        return RedirectResponse(f"{FRONTEND_URL}/?google_error=google_login_failed")

@app.get("/api/notes/date/{note_date}")
def get_notes(note_date:date,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    notes=db.query(Note).filter(
        Note.user_id==user.id,
        Note.note_date==note_date,
        Note.is_archived==False
    ).order_by(Note.is_pinned.desc(),Note.created_at.desc()).all()

    return [note_response(note) for note in notes]

@app.get("/api/notes/search")
def search_notes(q:str,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    query=q.strip()

    if not query:
        return []

    pattern=f"%{query}%"

    notes=db.query(Note).filter(
        Note.user_id==user.id,
        Note.is_archived==False,
        or_(
            Note.title.ilike(pattern),
            Note.content.ilike(pattern),
            Note.tags.ilike(pattern),
            Note.category.ilike(pattern)
        )
    ).order_by(Note.updated_at.desc()).all()

    return [note_response(note) for note in notes]

@app.get("/api/notes/{note_id}")
def get_note(note_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    note=db.query(Note).filter(
        Note.id==note_id,
        Note.user_id==user.id
    ).first()

    if not note:
        raise HTTPException(status_code=404,detail="Note not found")

    return note_response(note)

@app.post("/api/notes",status_code=201)
def create_note(data:NoteCreate,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    note=Note(
        user_id=user.id,
        note_date=data.note_date,
        title=data.title.strip(),
        category=data.category.strip().upper(),
        content=data.content,
        tags=",".join([tag.strip() for tag in data.tags if tag.strip()]),
        is_pinned=data.is_pinned,
        is_archived=False
    )

    db.add(note)
    db.commit()
    db.refresh(note)

    return note_response(note)

@app.put("/api/notes/{note_id}")
def update_note(note_id:int,data:NoteUpdate,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    note=db.query(Note).filter(
        Note.id==note_id,
        Note.user_id==user.id
    ).first()

    if not note:
        raise HTTPException(status_code=404,detail="Note not found")

    if data.title is not None:
        note.title=note.title if not data.title.strip() else data.title.strip()

    if data.category is not None:
        note.category=data.category.strip().upper()

    if data.content is not None:
        note.content=data.content

    if data.tags is not None:
        note.tags=",".join([tag.strip() for tag in data.tags if tag.strip()])

    if data.note_date is not None:
        note.note_date=data.note_date

    if data.is_pinned is not None:
        note.is_pinned=data.is_pinned

    if data.is_archived is not None:
        note.is_archived=data.is_archived

    version=NoteVersion(
        note_id=note.id,
        title=note.title,
        category=note.category,
        content=note.content,
        tags=note.tags
    )

    db.add(version)
    db.commit()
    db.refresh(note)

    return note_response(note)

@app.delete("/api/notes/{note_id}")
def delete_note(note_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    note=db.query(Note).filter(
        Note.id==note_id,
        Note.user_id==user.id
    ).first()

    if not note:
        raise HTTPException(status_code=404,detail="Note not found")

    db.delete(note)
    db.commit()

    return {"message":"Note deleted successfully"}

@app.patch("/api/notes/{note_id}/pin")
def toggle_pin(note_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    note=db.query(Note).filter(
        Note.id==note_id,
        Note.user_id==user.id
    ).first()

    if not note:
        raise HTTPException(status_code=404,detail="Note not found")

    note.is_pinned=not note.is_pinned
    db.commit()
    db.refresh(note)

    return note_response(note)

@app.patch("/api/notes/{note_id}/archive")
def toggle_archive(note_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    note=db.query(Note).filter(
        Note.id==note_id,
        Note.user_id==user.id
    ).first()

    if not note:
        raise HTTPException(status_code=404,detail="Note not found")

    note.is_archived=not note.is_archived
    db.commit()
    db.refresh(note)

    return note_response(note)

@app.get("/api/notes/archived")
def get_archived_notes(db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    notes=db.query(Note).filter(
        Note.user_id==user.id,
        Note.is_archived==True
    ).order_by(Note.updated_at.desc()).all()

    return [note_response(note) for note in notes]

@app.get("/api/notes/{note_id}/history")
def get_note_history(note_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    note=db.query(Note).filter(
        Note.id==note_id,
        Note.user_id==user.id
    ).first()

    if not note:
        raise HTTPException(status_code=404,detail="Note not found")

    versions=db.query(NoteVersion).filter(
        NoteVersion.note_id==note.id
    ).order_by(NoteVersion.created_at.desc()).all()

    return [
        {
            "id":version.id,
            "noteId":version.note_id,
            "title":version.title,
            "category":version.category,
            "content":version.content,
            "tags":[tag.strip() for tag in (version.tags or "").split(",") if tag.strip()],
            "createdAt":version.created_at
        }
        for version in versions
    ]

@app.get("/api/tasks/date/{task_date}")
def get_tasks(task_date:date,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    # When today's diary is opened, unfinished tasks from older dates are
    # carried forward to today. This is idempotent and creates no duplicates.
    today=date.today()

    if task_date==today:
        overdue_tasks=db.query(Task).filter(
            Task.user_id==user.id,
            Task.task_date<today,
            Task.completed==False
        ).all()

        for task in overdue_tasks:
            task.task_date=today

        if overdue_tasks:
            db.commit()

    tasks=db.query(Task).filter(
        Task.user_id==user.id,
        Task.task_date==task_date
    ).order_by(Task.completed.asc(),Task.created_at.asc()).all()

    return [task_response(task) for task in tasks]

@app.get("/api/tasks/{task_id}")
def get_task(task_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    task=db.query(Task).filter(
        Task.id==task_id,
        Task.user_id==user.id
    ).first()

    if not task:
        raise HTTPException(status_code=404,detail="Task not found")

    return task_response(task)

@app.post("/api/tasks",status_code=201)
def create_task(data:TaskCreate,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    task=Task(
        user_id=user.id,
        task_date=data.task_date,
        title=data.title.strip(),
        description=data.description.strip() if data.description else None,
        completed=False
    )

    db.add(task)
    db.commit()
    db.refresh(task)

    return task_response(task)

@app.patch("/api/tasks/{task_id}")
def update_task(task_id:int,data:TaskUpdate,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    task=db.query(Task).filter(
        Task.id==task_id,
        Task.user_id==user.id
    ).first()

    if not task:
        raise HTTPException(status_code=404,detail="Task not found")

    if data.task_date is not None:
        task.task_date=data.task_date

    if data.title is not None:
        task.title=data.title.strip()

    if data.description is not None:
        task.description=data.description.strip() or None

    if data.completed is not None:
        task.completed=data.completed

    db.commit()
    db.refresh(task)

    return task_response(task)

@app.delete("/api/tasks/{task_id}")
def delete_task(task_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    task=db.query(Task).filter(
        Task.id==task_id,
        Task.user_id==user.id
    ).first()

    if not task:
        raise HTTPException(status_code=404,detail="Task not found")

    db.delete(task)
    db.commit()

    return {"message":"Task deleted successfully"}

@app.get("/api/dashboard")
def dashboard(db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    total_notes=db.query(Note).filter(
        Note.user_id==user.id,
        Note.is_archived==False
    ).count()

    pinned_notes=db.query(Note).filter(
        Note.user_id==user.id,
        Note.is_pinned==True,
        Note.is_archived==False
    ).count()

    total_tasks=db.query(Task).filter(
        Task.user_id==user.id
    ).count()

    completed_tasks=db.query(Task).filter(
        Task.user_id==user.id,
        Task.completed==True
    ).count()

    learning_notes=db.query(Note).filter(
        Note.user_id==user.id,
        Note.category=="LEARNING",
        Note.is_archived==False
    ).count()

    bug_notes=db.query(Note).filter(
        Note.user_id==user.id,
        Note.category=="BUG",
        Note.is_archived==False
    ).count()

    project_notes=db.query(Note).filter(
        Note.user_id==user.id,
        Note.category=="PROJECT",
        Note.is_archived==False
    ).count()

    return {
        "totalNotes":total_notes,
        "pinnedNotes":pinned_notes,
        "totalTasks":total_tasks,
        "completedTasks":completed_tasks,
        "pendingTasks":total_tasks-completed_tasks,
        "learningNotes":learning_notes,
        "bugNotes":bug_notes,
        "projectNotes":project_notes
    }

@app.post("/api/notes/{note_id}/attachments",status_code=201)
async def upload_attachment(note_id:int,file:UploadFile=File(...),db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    note=db.query(Note).filter(
        Note.id==note_id,
        Note.user_id==user.id
    ).first()

    if not note:
        raise HTTPException(status_code=404,detail="Note not found")

    safe_name=Path(file.filename or "file").name
    unique_name=f"{secrets.token_hex(12)}_{safe_name}"
    object_path=f"user_{user.id}/note_{note.id}/{unique_name}"
    content=await file.read()

    try:
        storage_upload(object_path,content,file.content_type)
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"Attachment storage failed: {error}"
        )

    attachment=Attachment(
        note_id=note.id,
        file_name=safe_name,
        file_path=object_path,
        file_type=file.content_type,
        file_size=len(content)
    )

    try:
        db.add(attachment)
        db.commit()
        db.refresh(attachment)
    except Exception:
        db.rollback()
        try:
            storage_delete(object_path)
        except Exception:
            pass
        raise HTTPException(status_code=500,detail="Attachment metadata could not be saved")

    return attachment_response(attachment)

@app.get("/api/notes/{note_id}/attachments")
def get_attachments(note_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    note=db.query(Note).filter(
        Note.id==note_id,
        Note.user_id==user.id
    ).first()

    if not note:
        raise HTTPException(status_code=404,detail="Note not found")

    attachments=db.query(Attachment).filter(
        Attachment.note_id==note.id
    ).order_by(Attachment.created_at.desc()).all()

    return [attachment_response(attachment) for attachment in attachments]

@app.delete("/api/attachments/{attachment_id}")
def delete_attachment(attachment_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    attachment=db.query(Attachment).join(
        Note,Note.id==Attachment.note_id
    ).filter(
        Attachment.id==attachment_id,
        Note.user_id==user.id
    ).first()

    if not attachment:
        raise HTTPException(status_code=404,detail="Attachment not found")

    try:
        storage_delete(attachment.file_path)
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"Attachment storage deletion failed: {error}"
        )

    db.delete(attachment)
    db.commit()

    return {"message":"Attachment deleted successfully"}

@app.get("/api/admin/users")
def admin_get_users(db:Session=Depends(get_db),admin:User=Depends(get_admin_user)):
    users=db.query(User).order_by(User.created_at.desc()).all()

    return [
        {
            "id":user.id,
            "name":user.name,
            "email":user.email,
            "isAdmin":user.is_admin,
            "isActive":user.is_active,
            "createdAt":user.created_at
        }
        for user in users
    ]

@app.patch("/api/admin/users/{user_id}/status")
def admin_change_user_status(user_id:int,data:AdminStatusRequest,db:Session=Depends(get_db),admin:User=Depends(get_admin_user)):
    user=db.query(User).filter(User.id==user_id).first()

    if not user:
        raise HTTPException(status_code=404,detail="User not found")

    if user.id==admin.id and not data.is_active:
        raise HTTPException(status_code=400,detail="Administrator cannot disable their own account")

    user.is_active=data.is_active
    db.commit()

    return {
        "message":"User status updated",
        "user":user_response(user)
    }

@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id:int,db:Session=Depends(get_db),admin:User=Depends(get_admin_user)):
    user=db.query(User).filter(User.id==user_id).first()

    if not user:
        raise HTTPException(status_code=404,detail="User not found")

    if user.id==admin.id:
        raise HTTPException(status_code=400,detail="Administrator cannot delete their own account")

    db.delete(user)
    db.commit()

    return {"message":"User deleted successfully"}