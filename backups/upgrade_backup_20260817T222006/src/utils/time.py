from datetime import datetime


def now_iso() -> str:
    return datetime.now().isoformat()


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def parse_timestamp(ts: str):
    if not ts:
        return None
    try:
        if "T" in ts:
            return datetime.fromisoformat(ts)
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except:
        return None


def days_ago(ts: str) -> int:
    dt = parse_timestamp(ts)
    if dt is None:
        return 0
    return (datetime.now() - dt).days