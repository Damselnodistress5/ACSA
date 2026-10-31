from typing import Any, Dict, List, Optional


def get_requirements_txt(dependencies: Optional[List[str]] = None) -> str:
    base_deps = [
        "fastapi>=0.111.0",
        "uvicorn[standard]>=0.30.1",
        "pydantic>=2.7.4",
        "pydantic-settings>=2.3.4",
        "sqlalchemy>=2.0.31",
        "pytest>=8.2.2",
        "httpx>=0.27.0",
        "python-multipart>=0.0.9",
    ]
    if dependencies:
        for d in dependencies:
            if d.strip() and not any(d.lower().startswith(b.split(">")[0].split("=")[0]) for b in base_deps):
                base_deps.append(d.strip())
    return "\n".join(base_deps) + "\n"


def get_config_py(project_name: str) -> str:
    return f"""from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "{project_name}"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    DEBUG: bool = True
    BASE_URL: str = "http://localhost:8000"
    DATABASE_URL: str = "sqlite:///./app.db"
    CORS_ORIGINS: List[str] = ["*"]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
"""


def get_database_py() -> str:
    return '''from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = "sqlite:///./app.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db() -> Generator:
    """FastAPI dependency yielding an isolated SQLAlchemy database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
'''


def get_readme_md(domain: Dict[str, Any], project_name: str) -> str:
    dtype = domain["type"]
    p_name = project_name or domain["title"]

    if dtype == "url_shortener":
        endpoints_table = (
            "| Method | Endpoint | Description |\n"
            "|---|---|---|\n"
            "| `GET` | `/` | API status, metadata, and available routes |\n"
            "| `GET` | `/health` | Service health probe |\n"
            "| `POST` | `/shorten` | Shorten a target URL with optional custom alias (saved in SQLite) |\n"
            "| `GET` | `/{short_code}` | Redirect (HTTP 307) to original URL and record click in SQLite |\n"
            "| `GET` | `/stats/{short_code}` | Return real-time access analytics from SQLite |\n"
            "| `GET` | `/urls` | List all tracked shortened URLs |\n"
            "| `DELETE` | `/urls/{short_code}` | Delete a short URL record from SQLite |"
        )
    elif dtype == "todo":
        endpoints_table = (
            "| Method | Endpoint | Description |\n"
            "|---|---|---|\n"
            "| `GET` | `/` | Root status and API documentation link |\n"
            "| `GET` | `/health` | Health probe reporting SQLite database state |\n"
            "| `GET` | `/todos` | List all todos with completed/priority filters |\n"
            "| `POST` | `/todos` | Create a new todo with SQLite persistence |\n"
            "| `GET` | `/todos/{id}` | Retrieve a specific todo item |\n"
            "| `PUT` | `/todos/{id}` | Update title, description, priority, or due date |\n"
            "| `PATCH` | `/todos/{id}/complete` | Toggle todo completion state |\n"
            "| `DELETE` | `/todos/{id}` | Permanently delete a todo item |"
        )
    elif dtype == "notes":
        endpoints_table = (
            "| Method | Endpoint | Description |\n"
            "|---|---|---|\n"
            "| `GET` | `/` | Service root and metadata |\n"
            "| `GET` | `/health` | Health probe |\n"
            "| `GET` | `/notes` | List notes with tag, category, and search query filters |\n"
            "| `POST` | `/notes` | Create a new note stored in SQLite |\n"
            "| `GET` | `/notes/{id}` | Retrieve note by ID |\n"
            "| `PUT` | `/notes/{id}` | Update note contents or tags |\n"
            "| `DELETE` | `/notes/{id}` | Delete note from SQLite |"
        )
    elif dtype == "blog":
        endpoints_table = (
            "| Method | Endpoint | Description |\n"
            "|---|---|---|\n"
            "| `GET` | `/` | Service root and metadata |\n"
            "| `GET` | `/health` | Health probe |\n"
            "| `GET` | `/posts` | List published blog posts |\n"
            "| `POST` | `/posts` | Create a new post in SQLite with automatic slug |\n"
            "| `GET` | `/posts/{id}` | Get post by numeric ID |\n"
            "| `GET` | `/posts/slug/{slug}` | Get post by unique URL slug |\n"
            "| `PUT` | `/posts/{id}` | Update post content or publishing status |\n"
            "| `DELETE` | `/posts/{id}` | Delete post from SQLite |"
        )
    else:
        pl = domain["plural_name"]
        endpoints_table = (
            "| Method | Endpoint | Description |\n"
            "|---|---|---|\n"
            "| `GET` | `/` | Service status and metadata |\n"
            "| `GET` | `/health` | Health probe |\n"
            f"| `GET` | `/{pl}` | List all {pl} records from SQLite |\n"
            f"| `POST` | `/{pl}` | Create a new {domain['singular_name']} in SQLite |\n"
            f"| `GET` | `/{pl}/{{id}}` | Get {domain['singular_name']} by ID |\n"
            f"| `PUT` | `/{pl}/{{id}}` | Update {domain['singular_name']} record |\n"
            f"| `DELETE` | `/{pl}/{{id}}` | Delete record from SQLite |"
        )

    return f"""# {p_name}

> {domain['description']}

## Architecture Overview

This project is built using a clean, production-grade layered architecture:

- **FastAPI Application**: High-performance asynchronous API endpoints with interactive Swagger UI at `/docs`.
- **SQLite Database**: Real SQLite persistence (`app.db`) managed via SQLAlchemy ORM with automatic table creation.
- **Pydantic Schemas**: Strict input validation, response serialization, and comprehensive error shielding.
- **Service Layer**: Dedicated business logic layer handling database transactions with `get_db` dependency injection.

## Database Persistence

- Engine: SQLite via SQLAlchemy (`sqlite:///./app.db`)
- Connection: Thread-safe connection arguments (`check_same_thread=False`)
- Automatic Migration: Tables are automatically created upon application startup using `Base.metadata.create_all`.

## API Endpoints

{endpoints_table}

## Setup & Running

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\\Scripts\\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the development server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open your browser at `http://localhost:8000/docs` to test all endpoints interactively.
"""


def get_models_py(domain: Dict[str, Any]) -> str:
    dtype = domain["type"]

    if dtype == "url_shortener":
        return '''from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.orm import declarative_base

try:
    from app.database import Base
except ImportError:
    try:
        from database import Base
    except ImportError:
        Base = declarative_base()


# --- SQLAlchemy SQLite Model ---
class URLRecord(Base):
    __tablename__ = "urls"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    short_code = Column(String(50), unique=True, index=True, nullable=False)
    original_url = Column(String(2048), nullable=False)
    clicks = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_accessed = Column(DateTime, nullable=True)


# --- Pydantic Schemas ---
class ShortenRequest(BaseModel):
    url: str = Field(..., min_length=4, max_length=2048, description="Original target URL", example="https://fastapi.tiangolo.com")
    custom_alias: Optional[str] = Field(None, min_length=3, max_length=30, description="Custom short code alias", example="docs")


class URLResponse(BaseModel):
    short_code: str
    short_url: str
    original_url: str
    clicks: int = 0
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class URLStats(BaseModel):
    short_code: str
    original_url: str
    clicks: int
    created_at: str
    last_accessed: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
'''
    elif dtype == "todo":
        return '''from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.orm import declarative_base

try:
    from app.database import Base
except ImportError:
    try:
        from database import Base
    except ImportError:
        Base = declarative_base()


# --- SQLAlchemy SQLite Model ---
class TodoModel(Base):
    __tablename__ = "todos"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    title = Column(String(200), nullable=False, index=True)
    description = Column(Text, default="")
    completed = Column(Boolean, default=False, nullable=False)
    priority = Column(String(20), default="medium", nullable=False)
    due_date = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


# --- Pydantic Schemas ---
class TodoBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, example="Buy groceries")
    description: Optional[str] = Field(default="", example="Milk, eggs, coffee")
    priority: Optional[str] = Field(default="medium", example="high")
    due_date: Optional[str] = Field(default=None, example="2026-10-15")


class TodoCreate(TodoBase):
    pass


class TodoUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    completed: Optional[bool] = None
    priority: Optional[str] = None
    due_date: Optional[str] = None


class TodoResponse(TodoBase):
    id: int
    completed: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
'''
    elif dtype == "notes":
        return '''from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.orm import declarative_base

try:
    from app.database import Base
except ImportError:
    try:
        from database import Base
    except ImportError:
        Base = declarative_base()


# --- SQLAlchemy SQLite Model ---
class NoteModel(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    title = Column(String(200), nullable=False, index=True)
    content = Column(Text, nullable=False)
    tags = Column(String(200), default="", nullable=False)
    category = Column(String(100), default="General", nullable=False)
    is_pinned = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


# --- Pydantic Schemas ---
class NoteBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, example="System Architecture")
    content: str = Field(..., min_length=1, example="Key architectural decisions and trade-offs.")
    tags: Optional[str] = Field(default="", example="architecture,design")
    category: Optional[str] = Field(default="General", example="Engineering")
    is_pinned: Optional[bool] = Field(default=False, example=False)


class NoteCreate(NoteBase):
    pass


class NoteUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    content: Optional[str] = None
    tags: Optional[str] = None
    category: Optional[str] = None
    is_pinned: Optional[bool] = None


class NoteResponse(NoteBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
'''
    elif dtype == "blog":
        return '''from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.orm import declarative_base

try:
    from app.database import Base
except ImportError:
    try:
        from database import Base
    except ImportError:
        Base = declarative_base()


# --- SQLAlchemy SQLite Model ---
class PostModel(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    title = Column(String(250), nullable=False, index=True)
    slug = Column(String(250), unique=True, index=True, nullable=False)
    content = Column(Text, nullable=False)
    author = Column(String(100), default="Admin", nullable=False)
    published = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


# --- Pydantic Schemas ---
class PostBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=250, example="Modern FastAPI Patterns")
    slug: Optional[str] = Field(None, example="modern-fastapi-patterns")
    content: str = Field(..., min_length=1, example="FastAPI makes building asynchronous Python services effortless.")
    author: Optional[str] = Field(default="Admin", example="Alice")
    published: Optional[bool] = Field(default=True, example=True)


class PostCreate(PostBase):
    pass


class PostUpdate(BaseModel):
    title: Optional[str] = None
    slug: Optional[str] = None
    content: Optional[str] = None
    author: Optional[str] = None
    published: Optional[bool] = None


class PostResponse(PostBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
'''
    else:
        ent = domain["entity_name"]
        tbl = domain["plural_name"]
        return f'''from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.orm import declarative_base

try:
    from app.database import Base
except ImportError:
    try:
        from database import Base
    except ImportError:
        Base = declarative_base()


# --- SQLAlchemy SQLite Model ---
class {ent}Model(Base):
    __tablename__ = "{tbl}"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(200), nullable=False, index=True)
    description = Column(Text, default="")
    status = Column(String(50), default="active", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


# --- Pydantic Schemas ---
class {ent}Base(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, example="Sample {ent}")
    description: Optional[str] = Field(default="", example="Details about this {domain['singular_name']}")
    status: Optional[str] = Field(default="active", example="active")


class {ent}Create({ent}Base):
    pass


class {ent}Update(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    status: Optional[str] = None


class {ent}Response({ent}Base):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
'''


def get_services_py(domain: Dict[str, Any]) -> str:
    dtype = domain["type"]

    if dtype == "url_shortener":
        return '''from typing import List, Optional
from datetime import datetime
import secrets
import string
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

try:
    from app.models import URLRecord
except ImportError:
    from models import URLRecord


class URLService:
    @staticmethod
    def generate_code(db: Session, length: int = 6) -> str:
        chars = string.ascii_letters + string.digits
        for _ in range(10):
            code = ''.join(secrets.choice(chars) for _ in range(length))
            exists = db.query(URLRecord).filter(URLRecord.short_code == code).first()
            if not exists:
                return code
        return secrets.token_hex(4)[:length]

    def shorten(self, db: Session, url: str, custom_alias: Optional[str] = None) -> URLRecord:
        target_url = str(url).strip()
        if not (target_url.startswith("http://") or target_url.startswith("https://")):
            target_url = f"https://{target_url}"

        if custom_alias:
            code = custom_alias.strip()
            existing = db.query(URLRecord).filter(URLRecord.short_code == code).first()
            if existing:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Alias '{code}' already in use")
        else:
            existing = db.query(URLRecord).filter(URLRecord.original_url == target_url).first()
            if existing:
                return existing
            code = self.generate_code(db)

        record = URLRecord(
            short_code=code,
            original_url=target_url,
            clicks=0,
            created_at=datetime.utcnow(),
            last_accessed=None,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return record

    def get_and_click(self, db: Session, short_code: str) -> str:
        record = db.query(URLRecord).filter(URLRecord.short_code == short_code).first()
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Short code '{short_code}' not found")
        record.clicks += 1
        record.last_accessed = datetime.utcnow()
        db.commit()
        return record.original_url

    def get_stats(self, db: Session, short_code: str) -> URLRecord:
        record = db.query(URLRecord).filter(URLRecord.short_code == short_code).first()
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Short code '{short_code}' not found")
        return record

    def list_all(self, db: Session, skip: int = 0, limit: int = 50) -> List[URLRecord]:
        return db.query(URLRecord).order_by(URLRecord.id.desc()).offset(skip).limit(limit).all()

    def delete(self, db: Session, short_code: str) -> bool:
        record = db.query(URLRecord).filter(URLRecord.short_code == short_code).first()
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Short code '{short_code}' not found")
        db.delete(record)
        db.commit()
        return True


url_service = URLService()
'''
    elif dtype == "todo":
        return '''from typing import List, Optional
from datetime import datetime
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

try:
    from app.models import TodoModel, TodoCreate, TodoUpdate
except ImportError:
    from models import TodoModel, TodoCreate, TodoUpdate


class TodoService:
    def list_todos(self, db: Session, completed: Optional[bool] = None, priority: Optional[str] = None) -> List[TodoModel]:
        q = db.query(TodoModel)
        if completed is not None:
            q = q.filter(TodoModel.completed == completed)
        if priority:
            q = q.filter(TodoModel.priority == priority.lower())
        return q.order_by(TodoModel.id.desc()).all()

    def get_todo(self, db: Session, todo_id: int) -> TodoModel:
        item = db.query(TodoModel).filter(TodoModel.id == todo_id).first()
        if not item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Todo {todo_id} not found")
        return item

    def create_todo(self, db: Session, data: TodoCreate) -> TodoModel:
        item = TodoModel(**data.model_dump())
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    def update_todo(self, db: Session, todo_id: int, data: TodoUpdate) -> TodoModel:
        item = self.get_todo(db, todo_id)
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(item, k, v)
        item.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(item)
        return item

    def toggle_completed(self, db: Session, todo_id: int) -> TodoModel:
        item = self.get_todo(db, todo_id)
        item.completed = not item.completed
        item.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(item)
        return item

    def delete_todo(self, db: Session, todo_id: int) -> bool:
        item = self.get_todo(db, todo_id)
        db.delete(item)
        db.commit()
        return True


todo_service = TodoService()
'''
    elif dtype == "notes":
        return '''from typing import List, Optional
from datetime import datetime
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

try:
    from app.models import NoteModel, NoteCreate, NoteUpdate
except ImportError:
    from models import NoteModel, NoteCreate, NoteUpdate


class NoteService:
    def list_notes(self, db: Session, tag: Optional[str] = None, category: Optional[str] = None, q: Optional[str] = None) -> List[NoteModel]:
        query = db.query(NoteModel)
        if category:
            query = query.filter(NoteModel.category.ilike(f"%{category}%"))
        if tag:
            query = query.filter(NoteModel.tags.ilike(f"%{tag}%"))
        if q:
            query = query.filter((NoteModel.title.ilike(f"%{q}%")) | (NoteModel.content.ilike(f"%{q}%")))
        return query.order_by(NoteModel.is_pinned.desc(), NoteModel.id.desc()).all()

    def get_note(self, db: Session, note_id: int) -> NoteModel:
        item = db.query(NoteModel).filter(NoteModel.id == note_id).first()
        if not item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Note {note_id} not found")
        return item

    def create_note(self, db: Session, data: NoteCreate) -> NoteModel:
        item = NoteModel(**data.model_dump())
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    def update_note(self, db: Session, note_id: int, data: NoteUpdate) -> NoteModel:
        item = self.get_note(db, note_id)
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(item, k, v)
        item.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(item)
        return item

    def delete_note(self, db: Session, note_id: int) -> bool:
        item = self.get_note(db, note_id)
        db.delete(item)
        db.commit()
        return True


note_service = NoteService()
'''
    elif dtype == "blog":
        return '''from typing import List, Optional
from datetime import datetime
import re
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

try:
    from app.models import PostModel, PostCreate, PostUpdate
except ImportError:
    from models import PostModel, PostCreate, PostUpdate


class PostService:
    def list_posts(self, db: Session, published_only: bool = True) -> List[PostModel]:
        q = db.query(PostModel)
        if published_only:
            q = q.filter(PostModel.published == True)
        return q.order_by(PostModel.id.desc()).all()

    def get_post(self, db: Session, post_id: int) -> PostModel:
        item = db.query(PostModel).filter(PostModel.id == post_id).first()
        if not item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Post {post_id} not found")
        return item

    def get_by_slug(self, db: Session, slug: str) -> PostModel:
        item = db.query(PostModel).filter(PostModel.slug == slug).first()
        if not item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Post with slug '{slug}' not found")
        return item

    def create_post(self, db: Session, data: PostCreate) -> PostModel:
        slug = data.slug or re.sub(r"[^a-zA-Z0-9]+", "-", data.title.lower()).strip("-")
        existing = db.query(PostModel).filter(PostModel.slug == slug).first()
        if existing:
            slug = f"{slug}-{int(datetime.utcnow().timestamp())}"
        item = PostModel(**data.model_dump())
        item.slug = slug
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    def update_post(self, db: Session, post_id: int, data: PostUpdate) -> PostModel:
        item = self.get_post(db, post_id)
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(item, k, v)
        item.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(item)
        return item

    def delete_post(self, db: Session, post_id: int) -> bool:
        item = self.get_post(db, post_id)
        db.delete(item)
        db.commit()
        return True


post_service = PostService()
'''
    else:
        ent = domain["entity_name"]
        return f'''from typing import List, Optional
from datetime import datetime
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

try:
    from app.models import {ent}Model, {ent}Create, {ent}Update
except ImportError:
    from models import {ent}Model, {ent}Create, {ent}Update


class {ent}Service:
    def list_all(self, db: Session, skip: int = 0, limit: int = 50) -> List[{ent}Model]:
        return db.query({ent}Model).order_by({ent}Model.id.desc()).offset(skip).limit(limit).all()

    def get_by_id(self, db: Session, item_id: int) -> {ent}Model:
        item = db.query({ent}Model).filter({ent}Model.id == item_id).first()
        if not item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{ent} {{item_id}} not found")
        return item

    def create(self, db: Session, data: {ent}Create) -> {ent}Model:
        item = {ent}Model(**data.model_dump())
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    def update(self, db: Session, item_id: int, data: {ent}Update) -> {ent}Model:
        item = self.get_by_id(db, item_id)
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(item, k, v)
        item.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(item)
        return item

    def delete(self, db: Session, item_id: int) -> bool:
        item = self.get_by_id(db, item_id)
        db.delete(item)
        db.commit()
        return True


{domain['singular_name']}_service = {ent}Service()
'''


def get_main_py(domain: Dict[str, Any], project_name: str) -> str:
    dtype = domain["type"]
    p_name = project_name or domain["title"]

    if dtype == "url_shortener":
        return f'''from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
import uvicorn

try:
    from app.database import Base, engine, get_db
    from app.models import ShortenRequest, URLRecord, URLResponse, URLStats
    from app.services import url_service
except ImportError:
    from database import Base, engine, get_db
    from models import ShortenRequest, URLRecord, URLResponse, URLStats
    from services import url_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        if db.query(URLRecord).count() == 0:
            demo = URLRecord(
                short_code="docs",
                original_url="https://fastapi.tiangolo.com",
                clicks=0,
                created_at=datetime.utcnow(),
            )
            db.add(demo)
            db.commit()
    finally:
        db.close()
    yield


app = FastAPI(
    title="{p_name}",
    description="High-performance URL Shortener and Redirection API with SQLite database persistence.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["Root"])
def read_root():
    return {{
        "service": "{p_name}",
        "database": "SQLite (sqlite:///./app.db)",
        "status": "online",
        "endpoints": {{
            "shorten": "POST /shorten",
            "redirect": "GET /{{short_code}}",
            "stats": "GET /stats/{{short_code}}",
            "list": "GET /urls",
        }},
        "docs": "/docs",
    }}


@app.get("/health", tags=["Health"])
def health_check(db: Session = Depends(get_db)):
    count = db.query(URLRecord).count()
    return {{"status": "healthy", "service": "URL Shortener", "database": "connected", "total_urls": count}}


@app.post("/shorten", response_model=URLResponse, status_code=status.HTTP_201_CREATED, tags=["URL Shortener"])
def shorten_url(payload: ShortenRequest, db: Session = Depends(get_db)):
    rec = url_service.shorten(db=db, url=payload.url, custom_alias=payload.custom_alias)
    return URLResponse(
        short_code=rec.short_code,
        short_url=f"http://localhost:8000/{{rec.short_code}}",
        original_url=rec.original_url,
        clicks=rec.clicks,
        created_at=rec.created_at.isoformat() if rec.created_at else datetime.utcnow().isoformat(),
    )


@app.get("/{{short_code}}", tags=["URL Shortener"])
def redirect_url(short_code: str, db: Session = Depends(get_db)):
    target_url = url_service.get_and_click(db=db, short_code=short_code)
    return RedirectResponse(url=target_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@app.get("/stats/{{short_code}}", response_model=URLStats, tags=["URL Shortener"])
def get_stats(short_code: str, db: Session = Depends(get_db)):
    rec = url_service.get_stats(db=db, short_code=short_code)
    return URLStats(
        short_code=rec.short_code,
        original_url=rec.original_url,
        clicks=rec.clicks,
        created_at=rec.created_at.isoformat() if rec.created_at else datetime.utcnow().isoformat(),
        last_accessed=rec.last_accessed.isoformat() if rec.last_accessed else None,
    )


@app.get("/urls", response_model=List[URLResponse], tags=["URL Shortener"])
def list_urls(skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), db: Session = Depends(get_db)):
    records = url_service.list_all(db=db, skip=skip, limit=limit)
    return [
        URLResponse(
            short_code=r.short_code,
            short_url=f"http://localhost:8000/{{r.short_code}}",
            original_url=r.original_url,
            clicks=r.clicks,
            created_at=r.created_at.isoformat() if r.created_at else datetime.utcnow().isoformat(),
        )
        for r in records
    ]


@app.delete("/urls/{{short_code}}", status_code=status.HTTP_204_NO_CONTENT, tags=["URL Shortener"])
def delete_url(short_code: str, db: Session = Depends(get_db)):
    url_service.delete(db=db, short_code=short_code)
    return None


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
'''
    elif dtype == "todo":
        return f'''from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import uvicorn

try:
    from app.database import Base, engine, get_db
    from app.models import TodoCreate, TodoModel, TodoResponse, TodoUpdate
    from app.services import todo_service
except ImportError:
    from database import Base, engine, get_db
    from models import TodoCreate, TodoModel, TodoResponse, TodoUpdate
    from services import todo_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        if db.query(TodoModel).count() == 0:
            db.add(TodoModel(
                title="Welcome to ACSA Todo",
                description="Persistent SQLite task management engineered by ACSA.",
                completed=False,
                priority="high",
            ))
            db.commit()
    finally:
        db.close()
    yield


app = FastAPI(
    title="{p_name}",
    description="High-performance Todo API with SQLite persistence engineered by ACSA.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["Root"])
def read_root():
    return {{"service": "{p_name}", "database": "SQLite", "status": "online", "docs": "/docs"}}


@app.get("/health", tags=["Health"])
def health_check(db: Session = Depends(get_db)):
    count = db.query(TodoModel).count()
    return {{"status": "healthy", "service": "Todo API", "database": "connected", "total_todos": count}}


@app.get("/todos", response_model=List[TodoResponse], tags=["Todos"])
def list_todos(completed: Optional[bool] = None, priority: Optional[str] = None, db: Session = Depends(get_db)):
    return todo_service.list_todos(db=db, completed=completed, priority=priority)


@app.get("/todos/{{todo_id}}", response_model=TodoResponse, tags=["Todos"])
def get_todo(todo_id: int, db: Session = Depends(get_db)):
    return todo_service.get_todo(db=db, todo_id=todo_id)


@app.post("/todos", response_model=TodoResponse, status_code=status.HTTP_201_CREATED, tags=["Todos"])
def create_todo(payload: TodoCreate, db: Session = Depends(get_db)):
    return todo_service.create_todo(db=db, data=payload)


@app.put("/todos/{{todo_id}}", response_model=TodoResponse, tags=["Todos"])
def update_todo(todo_id: int, payload: TodoUpdate, db: Session = Depends(get_db)):
    return todo_service.update_todo(db=db, todo_id=todo_id, data=payload)


@app.patch("/todos/{{todo_id}}/complete", response_model=TodoResponse, tags=["Todos"])
def toggle_todo_complete(todo_id: int, db: Session = Depends(get_db)):
    return todo_service.toggle_completed(db=db, todo_id=todo_id)


@app.delete("/todos/{{todo_id}}", status_code=status.HTTP_204_NO_CONTENT, tags=["Todos"])
def delete_todo(todo_id: int, db: Session = Depends(get_db)):
    todo_service.delete_todo(db=db, todo_id=todo_id)
    return None


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
'''
    elif dtype == "notes":
        return f'''from contextlib import asynccontextmanager
from typing import List, Optional
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import uvicorn

try:
    from app.database import Base, engine, get_db
    from app.models import NoteCreate, NoteModel, NoteResponse, NoteUpdate
    from app.services import note_service
except ImportError:
    from database import Base, engine, get_db
    from models import NoteCreate, NoteModel, NoteResponse, NoteUpdate
    from services import note_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        if db.query(NoteModel).count() == 0:
            db.add(NoteModel(
                title="Welcome Note",
                content="Your notes are safely persisted in SQLite.",
                tags="welcome,fastapi",
                category="General",
                is_pinned=True,
            ))
            db.commit()
    finally:
        db.close()
    yield


app = FastAPI(
    title="{p_name}",
    description="High-performance Notes API with SQLite persistence engineered by ACSA.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["Root"])
def read_root():
    return {{"service": "{p_name}", "database": "SQLite", "status": "online", "docs": "/docs"}}


@app.get("/health", tags=["Health"])
def health_check(db: Session = Depends(get_db)):
    count = db.query(NoteModel).count()
    return {{"status": "healthy", "service": "Notes API", "database": "connected", "total_notes": count}}


@app.get("/notes", response_model=List[NoteResponse], tags=["Notes"])
def list_notes(tag: Optional[str] = None, category: Optional[str] = None, q: Optional[str] = None, db: Session = Depends(get_db)):
    return note_service.list_notes(db=db, tag=tag, category=category, q=q)


@app.get("/notes/{{note_id}}", response_model=NoteResponse, tags=["Notes"])
def get_note(note_id: int, db: Session = Depends(get_db)):
    return note_service.get_note(db=db, note_id=note_id)


@app.post("/notes", response_model=NoteResponse, status_code=status.HTTP_201_CREATED, tags=["Notes"])
def create_note(payload: NoteCreate, db: Session = Depends(get_db)):
    return note_service.create_note(db=db, data=payload)


@app.put("/notes/{{note_id}}", response_model=NoteResponse, tags=["Notes"])
def update_note(note_id: int, payload: NoteUpdate, db: Session = Depends(get_db)):
    return note_service.update_note(db=db, note_id=note_id, data=payload)


@app.delete("/notes/{{note_id}}", status_code=status.HTTP_204_NO_CONTENT, tags=["Notes"])
def delete_note(note_id: int, db: Session = Depends(get_db)):
    note_service.delete_note(db=db, note_id=note_id)
    return None


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
'''
    elif dtype == "blog":
        return f'''from contextlib import asynccontextmanager
from typing import List, Optional
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import uvicorn

try:
    from app.database import Base, engine, get_db
    from app.models import PostCreate, PostModel, PostResponse, PostUpdate
    from app.services import post_service
except ImportError:
    from database import Base, engine, get_db
    from models import PostCreate, PostModel, PostResponse, PostUpdate
    from services import post_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        if db.query(PostModel).count() == 0:
            db.add(PostModel(
                title="Hello World from ACSA",
                slug="hello-world-acsa",
                content="This publishing service uses real SQLite persistence.",
                author="ACSA Agent",
                published=True,
            ))
            db.commit()
    finally:
        db.close()
    yield


app = FastAPI(
    title="{p_name}",
    description="High-performance Blog API with SQLite persistence engineered by ACSA.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["Root"])
def read_root():
    return {{"service": "{p_name}", "database": "SQLite", "status": "online", "docs": "/docs"}}


@app.get("/health", tags=["Health"])
def health_check(db: Session = Depends(get_db)):
    count = db.query(PostModel).count()
    return {{"status": "healthy", "service": "Blog API", "database": "connected", "total_posts": count}}


@app.get("/posts", response_model=List[PostResponse], tags=["Posts"])
def list_posts(published_only: bool = True, db: Session = Depends(get_db)):
    return post_service.list_posts(db=db, published_only=published_only)


@app.get("/posts/{{post_id}}", response_model=PostResponse, tags=["Posts"])
def get_post(post_id: int, db: Session = Depends(get_db)):
    return post_service.get_post(db=db, post_id=post_id)


@app.get("/posts/slug/{{slug}}", response_model=PostResponse, tags=["Posts"])
def get_post_by_slug(slug: str, db: Session = Depends(get_db)):
    return post_service.get_by_slug(db=db, slug=slug)


@app.post("/posts", response_model=PostResponse, status_code=status.HTTP_201_CREATED, tags=["Posts"])
def create_post(payload: PostCreate, db: Session = Depends(get_db)):
    return post_service.create_post(db=db, data=payload)


@app.put("/posts/{{post_id}}", response_model=PostResponse, tags=["Posts"])
def update_post(post_id: int, payload: PostUpdate, db: Session = Depends(get_db)):
    return post_service.update_post(db=db, post_id=post_id, data=payload)


@app.delete("/posts/{{post_id}}", status_code=status.HTTP_204_NO_CONTENT, tags=["Posts"])
def delete_post(post_id: int, db: Session = Depends(get_db)):
    post_service.delete_post(db=db, post_id=post_id)
    return None


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
'''
    else:
        ent = domain["entity_name"]
        pl = domain["plural_name"]
        sn = domain["singular_name"]
        return f'''from contextlib import asynccontextmanager
from typing import List, Optional
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import uvicorn

try:
    from app.database import Base, engine, get_db
    from app.models import {ent}Create, {ent}Model, {ent}Response, {ent}Update
    from app.services import {sn}_service
except ImportError:
    from database import Base, engine, get_db
    from models import {ent}Create, {ent}Model, {ent}Response, {ent}Update
    from services import {sn}_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        if db.query({ent}Model).count() == 0:
            db.add({ent}Model(name="Sample {ent}", description="SQLite persistence test", status="active"))
            db.commit()
    finally:
        db.close()
    yield


app = FastAPI(
    title="{p_name}",
    description="{domain['description']}",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["Root"])
def read_root():
    return {{"service": "{p_name}", "database": "SQLite", "status": "online", "docs": "/docs"}}


@app.get("/health", tags=["Health"])
def health_check(db: Session = Depends(get_db)):
    count = db.query({ent}Model).count()
    return {{"status": "healthy", "service": "{p_name}", "database": "connected", "total_records": count}}


@app.get("/{pl}", response_model=List[{ent}Response], tags=["{ent}"])
def list_records(skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), db: Session = Depends(get_db)):
    return {sn}_service.list_all(db=db, skip=skip, limit=limit)


@app.get("/{pl}/{{item_id}}", response_model={ent}Response, tags=["{ent}"])
def get_record(item_id: int, db: Session = Depends(get_db)):
    return {sn}_service.get_by_id(db=db, item_id=item_id)


@app.post("/{pl}", response_model={ent}Response, status_code=status.HTTP_201_CREATED, tags=["{ent}"])
def create_record(payload: {ent}Create, db: Session = Depends(get_db)):
    return {sn}_service.create(db=db, data=payload)


@app.put("/{pl}/{{item_id}}", response_model={ent}Response, tags=["{ent}"])
def update_record(item_id: int, payload: {ent}Update, db: Session = Depends(get_db)):
    return {sn}_service.update(db=db, item_id=item_id, data=payload)


@app.delete("/{pl}/{{item_id}}", status_code=status.HTTP_204_NO_CONTENT, tags=["{ent}"])
def delete_record(item_id: int, db: Session = Depends(get_db)):
    {sn}_service.delete(db=db, item_id=item_id)
    return None


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
'''
