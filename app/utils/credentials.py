import os
import json
import tempfile
import re

def setup_google_credentials() -> None:
    # Resolve relative GOOGLE_APPLICATION_CREDENTIALS path to absolute path for GCP client libs,
    # or parse and write to a temp file if it contains the raw JSON string.
    google_creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not google_creds:
        return

    google_creds = google_creds.strip()
    # Strip matching outer quotes if present
    for quote in ("'", '"'):
        if google_creds.startswith(quote) and google_creds.endswith(quote):
            google_creds = google_creds[1:-1].strip()
            break

    if google_creds.startswith("{") and google_creds.endswith("}"):
        try:
            creds_dict = json.loads(google_creds)
            project_id = creds_dict.get("project_id", "default")
            project_id_clean = re.sub(r'[^a-zA-Z0-9_\-]', '', project_id)
            temp_dir = tempfile.gettempdir()
            creds_path = os.path.join(temp_dir, f"gcp_credentials_{project_id_clean}.json")
            with open(creds_path, "w", encoding="utf-8") as f:
                json.dump(creds_dict, f, indent=2)
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
        except Exception as e:
            print(f"Error parsing GOOGLE_APPLICATION_CREDENTIALS as JSON: {e}")
    elif not os.path.isabs(google_creds):
        abs_path = os.path.abspath(google_creds)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = abs_path
