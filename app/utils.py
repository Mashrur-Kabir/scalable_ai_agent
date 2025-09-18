import uuid, re, json

def make_request_id():
    return uuid.uuid4().hex


def extract_json_from_text(s: str):
    # try direct load
    try:
        return json.loads(s)
    except Exception:
        pass
    # fallback: extract {...} or [ ... ] block
    m = re.search(r"(\{.*\}|\[.*\])", s, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            return None
    return None