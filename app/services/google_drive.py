import httpx

from app.storage_settings import storage_settings


GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


class GoogleDriveError(RuntimeError):
    """A safe, provider-level Google Drive integration error."""


class GoogleReauthorizationRequired(GoogleDriveError):
    """The stored Google authorization can no longer be refreshed."""


def refresh_google_access_token(refresh_token: str) -> dict:
    """Exchange a stored refresh token for a short-lived access token."""

    if not refresh_token:
        raise ValueError("A refresh token is required.")

    client_id = storage_settings.google_client_id
    client_secret = storage_settings.google_client_secret

    if not client_id or client_secret is None:
        raise GoogleDriveError(
            "Google OAuth credentials are not configured."
        )

    try:
        response = httpx.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret.get_secret_value(),
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=15.0,
        )
    except httpx.TimeoutException as exc:
        raise GoogleDriveError(
            "Google token refresh timed out."
        ) from exc
    except httpx.RequestError as exc:
        raise GoogleDriveError(
            "Google token service is temporarily unavailable."
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise GoogleDriveError(
            "Google returned an invalid token response."
        ) from exc

    if response.status_code != 200:
        if payload.get("error") == "invalid_grant":
            raise GoogleReauthorizationRequired(
                "The Google connection needs to be authorized again."
            )

        raise GoogleDriveError(
            "Google rejected the token refresh request."
        )

    access_token = payload.get("access_token")
    expires_in = payload.get("expires_in")

    if not isinstance(access_token, str) or not access_token:
        raise GoogleDriveError(
            "Google's response did not contain an access token."
        )

    if not isinstance(expires_in, (int, float)) or expires_in <= 0:
        raise GoogleDriveError(
            "Google's response contained an invalid token lifetime."
        )

    return {
        "access_token": access_token,
        "expires_in": expires_in,
        "scope": payload.get("scope"),
    }


GOOGLE_FILES_URL = "https://www.googleapis.com/drive/v3/files"


def list_google_drive_files(
    access_token: str,
    page_size: int = 50,
    page_token: str | None = None,
) -> dict:
    """List accessible Drive file metadata, returning one page at a time."""

    if not access_token:
        raise ValueError("An access token is required.")

    if not 1 <= page_size <= 100:
        raise ValueError("Page size must be between 1 and 100.")

    params = {
        "pageSize": page_size,
        "q": "trashed = false",
        "orderBy": "modifiedTime desc",
        "fields": (
            "nextPageToken,"
            "files(id,name,mimeType,modifiedTime,size,webViewLink)"
        ),
    }

    if page_token:
        params["pageToken"] = page_token

    try:
        response = httpx.get(
            GOOGLE_FILES_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
            timeout=20.0,
        )
    except httpx.TimeoutException as exc:
        raise GoogleDriveError(
            "Google Drive file listing timed out."
        ) from exc
    except httpx.RequestError as exc:
        raise GoogleDriveError(
            "Google Drive is temporarily unavailable."
        ) from exc

    if response.status_code == 401:
        raise GoogleDriveError(
            "The Google access token is invalid or expired."
        )

    if response.status_code == 403:
        raise GoogleDriveError(
            "Google denied access to the requested Drive files."
        )

    if response.status_code == 429 or response.status_code >= 500:
        raise GoogleDriveError(
            "Google Drive is temporarily unavailable."
        )

    if response.status_code != 200:
        raise GoogleDriveError(
            "Google Drive rejected the file-listing request."
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise GoogleDriveError(
            "Google Drive returned an invalid response."
        ) from exc

    files = payload.get("files")
    if not isinstance(files, list):
        raise GoogleDriveError(
            "Google Drive returned an unexpected file listing."
        )

    return {
        "files": files,
        "next_page_token": payload.get("nextPageToken"),
    }


GOOGLE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
GOOGLE_FILE_URL = "https://www.googleapis.com/drive/v3/files"


def upload_google_drive_file(
    access_token: str,
    filename: str,
    content_type: str,
    file_content: bytes,
) -> dict:
    """Upload a binary file to Google Drive using a resumable session."""

    if not access_token or not filename or not content_type:
        raise ValueError("Token, filename, and content type are required.")

    if not file_content:
        raise ValueError("The uploaded file cannot be empty.")

    metadata = {"name": filename}

    try:
        session_response = httpx.post(
            GOOGLE_UPLOAD_URL,
            params={
                "uploadType": "resumable",
                "fields": "id,name,mimeType,size,webViewLink",
            },
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": content_type,
                "X-Upload-Content-Length": str(len(file_content)),
            },
            json=metadata,
            timeout=20.0,
        )
    except httpx.TimeoutException as exc:
        raise GoogleDriveError("Google upload initialization timed out.") from exc
    except httpx.RequestError as exc:
        raise GoogleDriveError("Google Drive is temporarily unavailable.") from exc

    if session_response.status_code not in (200, 201):
        if session_response.status_code == 401:
            raise GoogleDriveError("The Google access token is invalid or expired.")
        if session_response.status_code == 403:
            raise GoogleDriveError("Google denied permission to upload this file.")
        raise GoogleDriveError("Google Drive could not initialize the upload.")

    upload_url = session_response.headers.get("Location")
    if not upload_url:
        raise GoogleDriveError("Google did not return an upload session URL.")

    try:
        upload_response = httpx.put(
            upload_url,
            content=file_content,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": content_type,
                "Content-Length": str(len(file_content)),
            },
            timeout=60.0,
        )
    except httpx.TimeoutException as exc:
        raise GoogleDriveError("Google file upload timed out.") from exc
    except httpx.RequestError as exc:
        raise GoogleDriveError("Google Drive is temporarily unavailable.") from exc

    if upload_response.status_code not in (200, 201):
        if upload_response.status_code == 401:
            raise GoogleDriveError("The Google access token is invalid or expired.")
        if upload_response.status_code == 403:
            raise GoogleDriveError("Google denied permission to upload this file.")
        raise GoogleDriveError("Google Drive could not complete the upload.")

    try:
        result = upload_response.json()
    except ValueError as exc:
        raise GoogleDriveError("Google returned invalid upload metadata.") from exc

    if not result.get("id"):
        raise GoogleDriveError("Google's upload response did not contain a file ID.")

    return result


def download_google_drive_file(
    access_token: str,
    file_id: str,
) -> tuple[bytes, str]:
    """Download binary file content and return its bytes and content type."""

    if not access_token or not file_id:
        raise ValueError("Access token and file ID are required.")

    try:
        response = httpx.get(
            f"{GOOGLE_FILE_URL}/{file_id}",
            params={"alt": "media"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=60.0,
        )
    except httpx.TimeoutException as exc:
        raise GoogleDriveError("Google file download timed out.") from exc
    except httpx.RequestError as exc:
        raise GoogleDriveError("Google Drive is temporarily unavailable.") from exc

    if response.status_code == 401:
        raise GoogleDriveError("The Google access token is invalid or expired.")
    if response.status_code == 403:
        raise GoogleDriveError("Google denied permission to download this file.")
    if response.status_code == 404:
        raise GoogleDriveError("The requested Google Drive file was not found.")
    if response.status_code != 200:
        raise GoogleDriveError("Google Drive could not download the file.")

    content_type = response.headers.get(
        "Content-Type",
        "application/octet-stream",
    ).split(";", 1)[0].strip()

    return response.content, content_type


def delete_google_drive_file(
    access_token: str,
    file_id: str,
) -> None:
    """Delete a Google Drive file when a subsequent operation fails."""

    if not access_token or not file_id:
        raise ValueError("Access token and file ID are required.")

    try:
        response = httpx.delete(
            f"{GOOGLE_FILE_URL}/{file_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20.0,
        )
    except httpx.TimeoutException as exc:
        raise GoogleDriveError("Google file cleanup timed out.") from exc
    except httpx.RequestError as exc:
        raise GoogleDriveError("Google file cleanup failed.") from exc

    if response.status_code not in (200, 204, 404):
        raise GoogleDriveError("Google could not clean up the uploaded file.")
