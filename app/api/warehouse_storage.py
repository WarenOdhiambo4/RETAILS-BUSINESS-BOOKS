
import logging
from datetime import date
from urllib.parse import urlencode
import hashlib
from app.database import engine
import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from app.services.document_content_extraction import extract_document_text
from google.auth.transport.requests import Request
from google.oauth2 import id_token

from app.database import engine
from app.security_dependencies import get_current_user
from app.services.storage_oauth_state import (
    consume_oauth_state,
    create_oauth_state,
)
from app.services.storage_tokens import (
    decrypt_refresh_token,
    encrypt_refresh_token,
)
from app.services.google_drive import (
    GoogleDriveError,
    GoogleReauthorizationRequired,
    delete_google_drive_file,
    download_google_drive_file,
    refresh_google_access_token,
    upload_google_drive_file,
)
from app.services.warehouse_storage_permissions import (
    require_warehouse_storage_manager,
)
from app.storage_settings import storage_settings


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/storage",
    tags=["Warehouse Storage"],
)

GOOGLE_AUTHORIZATION_URL = (
    "https://accounts.google.com/o/oauth2/v2/auth"
)
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

GOOGLE_SCOPES = (
    "openid email "
    "https://www.googleapis.com/auth/drive.file"
)
GOOGLE_DRIVE_FILE_SCOPE = (
    "https://www.googleapis.com/auth/drive.file"
)


@router.get("/google/connect")
def start_google_drive_connection(
    warehouse_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Return a Google authorization URL for an authorized warehouse."""

    require_warehouse_storage_manager(
        current_user=current_user,
        warehouse_id=warehouse_id,
    )

    client_id = storage_settings.google_client_id
    redirect_uri = storage_settings.google_redirect_uri

    if not client_id or not redirect_uri:
        raise HTTPException(
            status_code=503,
            detail="Google Drive integration is not configured.",
        )

    try:
        state = create_oauth_state(
            warehouse_id=warehouse_id,
            user_id=int(current_user["UserID"]),
            provider="GoogleDrive",
        )
    except SQLAlchemyError as exc:
        logger.exception("Could not create Google OAuth state.")
        raise HTTPException(
            status_code=503,
            detail="Could not start the Google Drive connection.",
        ) from exc

    parameters = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }

    return {
        "authorization_url": (
            f"{GOOGLE_AUTHORIZATION_URL}?"
            f"{urlencode(parameters)}"
        )
    }


@router.get("/google/callback")
def google_drive_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """Validate Google's response and save a verified connection."""

    if not state:
        raise HTTPException(
            status_code=400,
            detail="OAuth state is missing or invalid.",
        )

    try:
        oauth_attempt = consume_oauth_state(
            state=state,
            provider="GoogleDrive",
        )
    except SQLAlchemyError as exc:
        logger.exception("Could not validate Google OAuth state.")
        raise HTTPException(
            status_code=503,
            detail="Could not validate the authorization attempt.",
        ) from exc

    if oauth_attempt is None:
        raise HTTPException(
            status_code=400,
            detail="OAuth state is invalid, expired, or already used.",
        )

    if error:
        raise HTTPException(
            status_code=400,
            detail="Google Drive authorization was not completed.",
        )

    if not code:
        raise HTTPException(
            status_code=400,
            detail="Google did not return an authorization code.",
        )

    warehouse_id = int(oauth_attempt["warehouse_id"])
    user_id = int(oauth_attempt["user_id"])

    # Permission might have changed while the user was on Google's page.
    require_warehouse_storage_manager(
        current_user={"UserID": user_id},
        warehouse_id=warehouse_id,
    )

    client_id = storage_settings.google_client_id
    client_secret = storage_settings.google_client_secret
    redirect_uri = storage_settings.google_redirect_uri

    if not client_id or not client_secret or not redirect_uri:
        raise HTTPException(
            status_code=503,
            detail="Google Drive integration is not configured.",
        )

    # Exchange the short-lived authorization code for Google tokens.
    try:
        response = httpx.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret.get_secret_value(),
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=20.0,
        )
        response.raise_for_status()
        token_data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Google authorization-code exchange failed.")
        raise HTTPException(
            status_code=502,
            detail="Google authorization could not be completed.",
        ) from exc

    returned_id_token = token_data.get("id_token")

    if not returned_id_token:
        raise HTTPException(
            status_code=502,
            detail="Google did not return an account identity token.",
        )

    # Verify the signed identity token and its intended audience.
    try:
        claims = id_token.verify_oauth2_token(
            returned_id_token,
            Request(),
            client_id,
        )
    except Exception as exc:
        logger.warning("Google ID-token verification failed.")
        raise HTTPException(
            status_code=401,
            detail="Google account identity could not be verified.",
        ) from exc

    provider_account_id = claims.get("sub")
    account_email = claims.get("email")
    email_verified = claims.get("email_verified") is True

    if not provider_account_id or not account_email or not email_verified:
        raise HTTPException(
            status_code=400,
            detail="Google did not provide a verified account identity.",
        )

    granted_scopes = token_data.get("scope", "")
    scope_set = set(granted_scopes.split())

    if GOOGLE_DRIVE_FILE_SCOPE not in scope_set:
        raise HTTPException(
            status_code=400,
            detail="Google Drive file access was not granted.",
        )

    new_refresh_token = token_data.get("refresh_token")

    try:
        with engine.begin() as connection:
            existing = connection.execute(
                text("""
                    SELECT
                        StorageConnectionID,
                        RefreshTokenEncrypted,
                        ConnectionStatus
                    FROM dbo.WarehouseStorageConnections
                    WHERE WarehouseID = :warehouse_id
                      AND Provider = N'GoogleDrive'
                      AND ProviderAccountID = :provider_account_id;
                """),
                {
                    "warehouse_id": warehouse_id,
                    "provider_account_id": provider_account_id,
                },
            ).mappings().first()

            if new_refresh_token:
                encrypted_refresh_token = encrypt_refresh_token(
                    new_refresh_token
                )
            elif (
                existing is not None
                and existing["ConnectionStatus"] == "Connected"
                and existing["RefreshTokenEncrypted"]
            ):
                # Google may omit a refresh token on repeat authorization.
                encrypted_refresh_token = existing[
                    "RefreshTokenEncrypted"
                ]
            else:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Google did not provide a refresh token. "
                        "No new connection was saved."
                    ),
                )

            if existing is not None:
                storage_connection_id = existing[
                    "StorageConnectionID"
                ]

                connection.execute(
                    text("""
                        UPDATE dbo.WarehouseStorageConnections
                        SET
                            ProviderAccountEmail = :account_email,
                            DisplayName = :display_name,
                            RefreshTokenEncrypted = :encrypted_token,
                            GrantedScopes = :granted_scopes,
                            ConnectionStatus = N'Connected',
                            ConnectedByUserID = :user_id,
                            ConnectedAt = SYSUTCDATETIME(),
                            UpdatedAt = SYSUTCDATETIME(),
                            DisconnectedAt = NULL
                        WHERE StorageConnectionID = :connection_id
                          AND WarehouseID = :warehouse_id;
                    """),
                    {
                        "account_email": account_email,
                        "display_name": claims.get("name"),
                        "encrypted_token": encrypted_refresh_token,
                        "granted_scopes": granted_scopes,
                        "user_id": user_id,
                        "connection_id": storage_connection_id,
                        "warehouse_id": warehouse_id,
                    },
                )
                audit_action = "GoogleDriveReconnected"
            else:
                storage_connection_id = connection.execute(
                    text("""
                        INSERT INTO dbo.WarehouseStorageConnections (
                            WarehouseID,
                            Provider,
                            ProviderAccountID,
                            ProviderAccountEmail,
                            DisplayName,
                            RefreshTokenEncrypted,
                            GrantedScopes,
                            ConnectionStatus,
                            ConnectedByUserID,
                            ConnectedAt,
                            UpdatedAt,
                            DisconnectedAt
                        )
                        OUTPUT INSERTED.StorageConnectionID
                        VALUES (
                            :warehouse_id,
                            N'GoogleDrive',
                            :provider_account_id,
                            :account_email,
                            :display_name,
                            :encrypted_token,
                            :granted_scopes,
                            N'Connected',
                            :user_id,
                            SYSUTCDATETIME(),
                            SYSUTCDATETIME(),
                            NULL
                        );
                    """),
                    {
                        "warehouse_id": warehouse_id,
                        "provider_account_id": provider_account_id,
                        "account_email": account_email,
                        "display_name": claims.get("name"),
                        "encrypted_token": encrypted_refresh_token,
                        "granted_scopes": granted_scopes,
                        "user_id": user_id,
                    },
                ).scalar_one()

                audit_action = "GoogleDriveConnected"

            connection.execute(
                text("""
                    INSERT INTO dbo.AuditLog (
                        UserID,
                        Action,
                        RecordType,
                        RecordID,
                        Reason
                    )
                    VALUES (
                        :user_id,
                        :action,
                        N'WarehouseStorageConnection',
                        :record_id,
                        :reason
                    );
                """),
                {
                    "user_id": str(user_id),
                    "action": audit_action,
                    "record_id": str(storage_connection_id),
                    "reason": (
                        "Google Drive account connected to warehouse "
                        f"{warehouse_id}."
                    ),
                },
            )

    except HTTPException:
        raise
    except IntegrityError as exc:
        logger.warning(
            "Google Drive connection conflicted with an existing record."
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "The Google Drive connection conflicted with an existing "
                "record. Please retry the connection."
            ),
        ) from exc
    except SQLAlchemyError as exc:
        logger.exception("Could not save the Google Drive connection.")
        raise HTTPException(
            status_code=503,
            detail="Could not save the Google Drive connection.",
        ) from exc
    except RuntimeError as exc:
        logger.exception("Refresh-token encryption failed.")
        raise HTTPException(
            status_code=503,
            detail="Secure token storage is temporarily unavailable.",
        ) from exc

    return {
        "status": "Connected",
        "provider": "GoogleDrive",
        "warehouse_id": warehouse_id,
        "connection_id": storage_connection_id,
        "account_email": account_email,
    }


@router.get("/connections")
def list_warehouse_storage_connections(
    warehouse_id: int,
    current_user: dict = Depends(get_current_user),
):
    """List safe storage-connection metadata for an authorized warehouse."""

    require_warehouse_storage_manager(
        current_user=current_user,
        warehouse_id=warehouse_id,
    )

    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text("""
                    SELECT
                        StorageConnectionID,
                        WarehouseID,
                        Provider,
                        ProviderAccountEmail,
                        DisplayName,
                        ConnectionStatus,
                        ConnectedAt,
                        UpdatedAt,
                        DisconnectedAt
                    FROM dbo.WarehouseStorageConnections
                    WHERE WarehouseID = :warehouse_id
                    ORDER BY UpdatedAt DESC, StorageConnectionID DESC;
                """),
                {"warehouse_id": warehouse_id},
            ).mappings().all()

    except SQLAlchemyError as exc:
        logger.exception(
            "Could not list storage connections for warehouse %s.",
            warehouse_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Storage connections are temporarily unavailable.",
        ) from exc

    return {
        "warehouse_id": warehouse_id,
        "connections": [
            {
                "connection_id": row["StorageConnectionID"],
                "provider": row["Provider"],
                "account_email": row["ProviderAccountEmail"],
                "display_name": row["DisplayName"],
                "status": row["ConnectionStatus"],
                "connected_at": row["ConnectedAt"],
                "updated_at": row["UpdatedAt"],
                "disconnected_at": row["DisconnectedAt"],
            }
            for row in rows
        ],
        "count": len(rows),
    }


MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@router.post("/documents/upload", status_code=201)
async def upload_warehouse_document(
    warehouse_id: int = Form(...),
    connection_id: int = Form(...),
    file_kind: str = Form(...),
    file: UploadFile = File(...),
    description: str | None = Form(None),
    current_user: dict = Depends(get_current_user),
):
    """Upload a warehouse document to an authorized Google Drive connection."""

    require_warehouse_storage_manager(
        current_user=current_user,
        warehouse_id=warehouse_id,
    )

    if connection_id <= 0:
        raise HTTPException(status_code=422, detail="Invalid storage connection ID.")

    if file_kind not in {"Document", "Image"}:
        raise HTTPException(
            status_code=422,
            detail="File kind must be Document or Image.",
        )

    if description is not None and len(description) > 1000:
        raise HTTPException(
            status_code=422,
            detail="Description cannot exceed 1000 characters.",
        )

    original_filename = (file.filename or "").replace("\\", "/").split("/")[-1]
    original_filename = original_filename.strip()

    if (
        not original_filename
        or original_filename in {".", ".."}
        or len(original_filename) > 255
        or any(ord(character) < 32 for character in original_filename)
    ):
        raise HTTPException(
            status_code=422,
            detail="Provide a valid filename of at most 255 characters.",
        )

    content_type = (
        file.content_type or "application/octet-stream"
    ).split(";", 1)[0].strip().lower()

    if len(content_type) > 150:
        raise HTTPException(status_code=422, detail="Content type is too long.")

    if file_kind == "Image" and not content_type.startswith("image/"):
        raise HTTPException(
            status_code=422,
            detail="Image files must have an image content type.",
        )

    if file_kind == "Document" and content_type.startswith("image/"):
        raise HTTPException(
            status_code=422,
            detail="Choose Image as the file kind for image files.",
        )

    file_content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()

    if not file_content:
        raise HTTPException(status_code=422, detail="The file cannot be empty.")

    if len(file_content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="The maximum upload size is 10 MB.",
        )

    try:
        with engine.connect() as connection:
            storage_connection = connection.execute(
                text("""
                    SELECT
                        StorageConnectionID,
                        RefreshTokenEncrypted
                    FROM dbo.WarehouseStorageConnections
                    WHERE StorageConnectionID = :connection_id
                      AND WarehouseID = :warehouse_id
                      AND Provider = N'GoogleDrive'
                      AND ConnectionStatus = N'Connected';
                """),
                {
                    "connection_id": connection_id,
                    "warehouse_id": warehouse_id,
                },
            ).mappings().first()

    except SQLAlchemyError as exc:
        logger.exception("Could not verify the selected storage connection.")
        raise HTTPException(
            status_code=503,
            detail="Storage connection verification is temporarily unavailable.",
        ) from exc

    if storage_connection is None:
        raise HTTPException(
            status_code=404,
            detail="No connected Google Drive account was found for this warehouse.",
        )

    encrypted_refresh_token = storage_connection["RefreshTokenEncrypted"]

    if not encrypted_refresh_token:
        raise HTTPException(
            status_code=409,
            detail="The Google Drive connection needs to be authorized again.",
        )

    try:
        refresh_token = decrypt_refresh_token(encrypted_refresh_token)
        token_data = refresh_google_access_token(refresh_token)
        access_token = token_data["access_token"]

    except GoogleReauthorizationRequired as exc:
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("""
                        UPDATE dbo.WarehouseStorageConnections
                        SET ConnectionStatus = N'NeedsReauthorization',
                            UpdatedAt = SYSUTCDATETIME()
                        WHERE StorageConnectionID = :connection_id
                          AND WarehouseID = :warehouse_id
                          AND ConnectionStatus = N'Connected';
                    """),
                    {
                        "connection_id": connection_id,
                        "warehouse_id": warehouse_id,
                    },
                )
        except SQLAlchemyError:
            logger.exception("Could not update the Google connection status.")

        raise HTTPException(
            status_code=409,
            detail="The Google Drive connection needs to be authorized again.",
        ) from exc

    except GoogleDriveError as exc:
        logger.warning("Google Drive token refresh failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="Could not authorize the Google Drive upload.",
        ) from exc

    except (RuntimeError, ValueError) as exc:
        logger.exception("Stored Google Drive credentials could not be used.")
        raise HTTPException(
            status_code=503,
            detail="Google Drive credentials are temporarily unavailable.",
        ) from exc

    try:
        uploaded_file = upload_google_drive_file(
            access_token=access_token,
            filename=original_filename,
            content_type=content_type,
            file_content=file_content,
        )

    except GoogleDriveError as exc:
        logger.warning("Google Drive upload failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="Google Drive could not complete the file upload.",
        ) from exc

    google_file_id = uploaded_file["id"]

    content_hash = hashlib.sha256(file_content).hexdigest()

    extraction = extract_document_text(
        filename=original_filename,
        file_content=file_content,
    )

    extracted_text = extraction.text
    indexing_status = extraction.status
    extraction_error = extraction.reason
    extracted_character_count = (
        len(extracted_text) if extracted_text is not None else None
    )

    try:
        with engine.begin() as connection:
            document_id = connection.execute(
                text("""
                    INSERT INTO dbo.WarehouseDocuments (
                        WarehouseID,
                        FileKind,
                        OriginalFileName,
                        StorageKey,
                        ContentType,
                        FileSizeBytes,
                        Description,
                        UploadedByUserID,
                        StorageConnectionID
                    )
                    OUTPUT INSERTED.WarehouseDocumentID
                    VALUES (
                        :warehouse_id,
                        :file_kind,
                        :original_filename,
                        :storage_key,
                        :content_type,
                        :file_size_bytes,
                        :description,
                        :user_id,
                        :connection_id
                    );
                """),
                {
                    "warehouse_id": warehouse_id,
                    "file_kind": file_kind,
                    "original_filename": original_filename,
                    "storage_key": google_file_id,
                    "content_type": content_type,
                    "file_size_bytes": len(file_content),
                    "description": description,
                    "user_id": current_user["UserID"],
                    "connection_id": connection_id,
                },
            ).scalar_one()

            connection.execute(
                text("""
                    INSERT INTO dbo.WarehouseDocumentContentIndex (
                        WarehouseDocumentID,
                        ExtractedText,
                        ContentHash,
                        ExtractedCharacterCount,
                        IndexingStatus,
                        LastAttemptAt,
                        ExtractedAt,
                        ExtractionError,
                        UpdatedAt
                    )
                    VALUES (
                        :document_id,
                        :extracted_text,
                        :content_hash,
                        :character_count,
                        :indexing_status,
                        SYSUTCDATETIME(),
                        CASE
                            WHEN :indexing_status = N'Extracted'
                            THEN SYSUTCDATETIME()
                            ELSE NULL
                        END,
                        :extraction_error,
                        SYSUTCDATETIME()
                    );
                """),
                {
                    "document_id": document_id,
                    "extracted_text": extracted_text,
                    "content_hash": content_hash,
                    "character_count": extracted_character_count,
                    "indexing_status": indexing_status,
                    "extraction_error": extraction_error,
                },
            )

    except SQLAlchemyError as exc:
        logger.exception(
            "Could not save uploaded document metadata for warehouse %s.",
            warehouse_id,
        )

        try:
            delete_google_drive_file(access_token, google_file_id)
        except GoogleDriveError:
            logger.exception(
                "Cleanup failed for Google Drive file ID %s.",
                google_file_id,
            )

        raise HTTPException(
            status_code=503,
            detail=(
                "The file reached Google Drive, but its database record "
                "could not be saved. Cleanup was attempted."
            ),
        ) from exc

    return {
        "status": "Uploaded",
        "warehouse_id": warehouse_id,
        "document_id": document_id,
        "connection_id": connection_id,
        "file_name": original_filename,
        "file_kind": file_kind,
        "content_type": content_type,
        "file_size_bytes": len(file_content),
    }


@router.get("/documents/{document_id}/download")
def download_warehouse_document(
    document_id: int,
    warehouse_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Download an active warehouse document from its connected Google Drive."""

    require_warehouse_storage_manager(
        current_user=current_user,
        warehouse_id=warehouse_id,
    )

    if document_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="Document ID must be greater than zero.",
        )

    try:
        with engine.connect() as connection:
            document = connection.execute(
                text("""
                    SELECT
                        d.WarehouseDocumentID,
                        d.WarehouseID,
                        d.OriginalFileName,
                        d.StorageKey,
                        d.ContentType,
                        d.FileSizeBytes,
                        c.StorageConnectionID,
                        c.RefreshTokenEncrypted
                    FROM dbo.WarehouseDocuments AS d
                    INNER JOIN dbo.WarehouseStorageConnections AS c
                        ON c.StorageConnectionID = d.StorageConnectionID
                       AND c.WarehouseID = d.WarehouseID
                    WHERE d.WarehouseDocumentID = :document_id
                      AND d.WarehouseID = :warehouse_id
                      AND d.IsActive = 1
                      AND c.Provider = N'GoogleDrive'
                      AND c.ConnectionStatus = N'Connected';
                """),
                {
                    "document_id": document_id,
                    "warehouse_id": warehouse_id,
                },
            ).mappings().first()

    except SQLAlchemyError as exc:
        logger.exception(
            "Could not retrieve document metadata for document %s.",
            document_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Document metadata is temporarily unavailable.",
        ) from exc

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="The active document was not found for this warehouse.",
        )

    encrypted_refresh_token = document["RefreshTokenEncrypted"]

    if not encrypted_refresh_token:
        raise HTTPException(
            status_code=409,
            detail="The Google Drive connection needs to be authorized again.",
        )

    try:
        refresh_token = decrypt_refresh_token(encrypted_refresh_token)
        token_data = refresh_google_access_token(refresh_token)
        access_token = token_data["access_token"]

    except GoogleReauthorizationRequired as exc:
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("""
                        UPDATE dbo.WarehouseStorageConnections
                        SET ConnectionStatus = N'NeedsReauthorization',
                            UpdatedAt = SYSUTCDATETIME()
                        WHERE StorageConnectionID = :connection_id
                          AND WarehouseID = :warehouse_id
                          AND ConnectionStatus = N'Connected';
                    """),
                    {
                        "connection_id": document["StorageConnectionID"],
                        "warehouse_id": warehouse_id,
                    },
                )
        except SQLAlchemyError:
            logger.exception(
                "Could not update the Google connection status after refresh failure."
            )

        raise HTTPException(
            status_code=409,
            detail="The Google Drive connection needs to be authorized again.",
        ) from exc

    except GoogleDriveError as exc:
        logger.warning(
            "Google Drive authorization failed for document %s: %s",
            document_id,
            exc,
        )
        raise HTTPException(
            status_code=502,
            detail="Google Drive could not authorize the download.",
        ) from exc

    except (RuntimeError, ValueError) as exc:
        logger.exception("Stored Google Drive credentials could not be used.")
        raise HTTPException(
            status_code=503,
            detail="Google Drive credentials are temporarily unavailable.",
        ) from exc

    try:
        file_content, _provider_content_type = download_google_drive_file(
            access_token=access_token,
            file_id=document["StorageKey"],
        )

    except GoogleDriveError as exc:
        logger.warning(
            "Google Drive download failed for document %s: %s",
            document_id,
            exc,
        )
        raise HTTPException(
            status_code=502,
            detail="Google Drive could not retrieve the requested file.",
        ) from exc

    if not file_content:
        raise HTTPException(
            status_code=502,
            detail="Google Drive returned an empty file.",
        )

    if len(file_content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=502,
            detail="The stored file exceeds the supported download size.",
        )

    try:
        with engine.begin() as connection:
            connection.execute(
                text("""
                    INSERT INTO dbo.AuditLog (
                        UserID,
                        Action,
                        RecordType,
                        RecordID,
                        Reason
                    )
                    VALUES (
                        :user_id,
                        N'WarehouseDocumentDownloaded',
                        N'WarehouseDocument',
                        :record_id,
                        :reason
                    );
                """),
                {
                    "user_id": str(current_user["UserID"]),
                    "record_id": str(document_id),
                    "reason": (
                        f"Downloaded {document['OriginalFileName']} "
                        f"from warehouse {warehouse_id}."
                    ),
                },
            )

    except SQLAlchemyError as exc:
        logger.exception(
            "Could not audit the download of document %s.",
            document_id,
        )
        raise HTTPException(
            status_code=503,
            detail="The download could not be completed because audit logging failed.",
        ) from exc

    from urllib.parse import quote

    filename = document["OriginalFileName"]
    safe_fallback = filename.replace('"', "'")
    disposition = (
        f'attachment; filename="{safe_fallback}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )

    return Response(
        content=file_content,
        media_type=document["ContentType"],
        headers={
            "Content-Disposition": disposition,
            "Content-Length": str(len(file_content)),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/documents")
def search_warehouse_documents(
    warehouse_id: int = Query(..., gt=0),
    q: str | None = Query(None, max_length=100),
    file_kind: str | None = Query(None),
    uploaded_from: date | None = Query(None),
    uploaded_to: date | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """Search active warehouse documents by filename, description, and filters."""

    require_warehouse_storage_manager(
        current_user=current_user,
        warehouse_id=warehouse_id,
    )

    if file_kind is not None and file_kind not in {"Document", "Image"}:
        raise HTTPException(
            status_code=422,
            detail="File kind must be Document or Image.",
        )

    if uploaded_from is not None and uploaded_to is not None:
        if uploaded_from > uploaded_to:
            raise HTTPException(
                status_code=422,
                detail="The start date cannot be later than the end date.",
            )

    search_text = q.strip() if q else None
    if not search_text:
        search_text = None

    parameters = {
        "warehouse_id": warehouse_id,
        "search_text": search_text,
        "file_kind": file_kind,
        "uploaded_from": uploaded_from,
        "uploaded_to": uploaded_to,
        "offset": (page - 1) * page_size,
        "page_size": page_size,
    }

    where_clause = """
        WHERE d.WarehouseID = :warehouse_id
          AND d.IsActive = 1
          AND (
              :search_text IS NULL
              OR CHARINDEX(:search_text, d.OriginalFileName) > 0
              OR CHARINDEX(
                  :search_text,
                  ISNULL(d.Description, N'')
              ) > 0
          )
          AND (:file_kind IS NULL OR d.FileKind = :file_kind)
          AND (
              :uploaded_from IS NULL
              OR d.UploadedAt >= :uploaded_from
          )
          AND (
              :uploaded_to IS NULL
              OR d.UploadedAt < DATEADD(DAY, 1, :uploaded_to)
          )
    """

    try:
        with engine.connect() as connection:
            total = connection.execute(
                text(f"""
                    SELECT COUNT_BIG(*)
                    FROM dbo.WarehouseDocuments AS d
                    {where_clause};
                """),
                parameters,
            ).scalar_one()

            rows = connection.execute(
                text(f"""
                    SELECT
                        d.WarehouseDocumentID,
                        d.OriginalFileName,
                        d.FileKind,
                        d.Description,
                        d.ContentType,
                        d.FileSizeBytes,
                        d.UploadedAt,
                        d.UploadedByUserID
                    FROM dbo.WarehouseDocuments AS d
                    {where_clause}
                    ORDER BY
                        d.UploadedAt DESC,
                        d.WarehouseDocumentID DESC
                    OFFSET :offset ROWS
                    FETCH NEXT :page_size ROWS ONLY;
                """),
                parameters,
            ).mappings().all()

    except SQLAlchemyError as exc:
        logger.exception(
            "Document search failed for warehouse %s.",
            warehouse_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Document search is temporarily unavailable.",
        ) from exc

    return {
        "warehouse_id": warehouse_id,
        "filters": {
            "query": search_text,
            "file_kind": file_kind,
            "uploaded_from": uploaded_from,
            "uploaded_to": uploaded_to,
        },
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_results": total,
            "total_pages": (total + page_size - 1) // page_size,
        },
        "results": [
            {
                "document_id": row["WarehouseDocumentID"],
                "file_name": row["OriginalFileName"],
                "file_kind": row["FileKind"],
                "description": row["Description"],
                "content_type": row["ContentType"],
                "file_size_bytes": row["FileSizeBytes"],
                "uploaded_at": row["UploadedAt"],
                "uploaded_by_user_id": row["UploadedByUserID"],
                "download_path": (
                    f"/api/v1/storage/documents/"
                    f"{row['WarehouseDocumentID']}/download"
                    f"?warehouse_id={warehouse_id}"
                ),
            }
            for row in rows
        ],
    }


