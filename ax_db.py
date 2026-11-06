
import os
import re
import functools
from datetime import date, datetime, timedelta
from pathlib import Path


ENV_FILE = Path(__file__).parent / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

DEFAULT_COMPANY = os.getenv("AX_COMPANY", "DAT").upper()
CONN_STR = (
    f"DRIVER={{{os.getenv('AX_SQL_DRIVER', 'ODBC Driver 11 for SQL Server')}}};"
    f"SERVER={os.getenv('AX_SQL_SERVER', '')};"
    f"DATABASE={os.getenv('AX_SQL_DATABASE', 'MicrosoftDynamicsAX')};"
    f"UID={os.getenv('AX_SQL_USER', '')};"
    f"PWD={os.getenv('AX_SQL_PASSWORD', '')};"
)
QUERY_TIMEOUT = int(os.getenv("AX_QUERY_TIMEOUT", "30"))


SALES_STATUS = {0: "None", 1: "Open order", 2: "Delivered", 3: "Invoiced", 4: "Canceled"}
SALES_TYPE = {0: "Journal", 1: "Quotation", 2: "Subscription", 3: "Sales order",
              4: "Return order", 5: "Blanket order", 6: "Item requirements"}
PURCH_STATUS = {0: "None", 1: "Open order", 2: "Received", 3: "Invoiced", 4: "Canceled"}


class NotConnected(Exception):
    """Raised when the AX SQL server can't be reached."""


class NeedClarification(Exception):
    """Raised inside a tool when we must ask the user something."""
    def __init__(self, question, options=None):
        super().__init__(question)
        self.question, self.options = question, options or []



def connect():
    try:
        import pyodbc
        conn = pyodbc.connect(CONN_STR, timeout=5, readonly=True)
        conn.timeout = QUERY_TIMEOUT
        return conn
    except Exception as e:
        raise NotConnected(str(e)[:200])


def _clean(val):
    """Convert SQL values to JSON-friendly types."""
    if isinstance(val, (datetime, date)):
        return val.isoformat(sep=" ")[:16] if isinstance(val, datetime) else val.isoformat()
    if val is not None and not isinstance(val, (str, int, float, bool)):
        return float(val)
    if isinstance(val, str):
        return val.strip()
    return val


def query(sql, params=(), max_rows=500):
    """Run a SELECT with ? parameters. Returns a list of dicts."""
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, list(params))
        cols = [c[0] for c in cur.description]
        return [{c: _clean(v) for c, v in zip(cols, row)} for row in cur.fetchmany(max_rows)]
    finally:
        conn.close()



def ok(data, **extra):
    return {"status": "ok", "source": "ax2012", "data": data, **extra}


def ask(question, options=None):
    return {"status": "need_clarification", "ask_user": question, "options": options or []}


def ax_tool(func):
    """Convert connection and SQL errors into structured tool results."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except NeedClarification as q:
            return ask(q.question, q.options)
        except NotConnected as e:
            return {"status": "not_connected", "source": "none",
                    "message": "The AX database is not reachable right now. "
                               "Tell the user; do not invent data.",
                    "reason": str(e)}
        except Exception as e:
            return {"status": "error", "message": str(e)[:300]}
    return wrapper



def company_of(company):
    return (company or DEFAULT_COMPANY).strip().upper()


def limit_of(limit, maximum):
    return max(1, min(int(limit or 1), maximum))


def date_range(from_date, to_date, default_days=30):
    """Parse 'YYYY-MM-DD' dates. Default = last N days."""
    try:
        end = date.fromisoformat(to_date) if to_date else date.today()
        start = date.fromisoformat(from_date) if from_date else end - timedelta(days=default_days)
    except ValueError:
        raise NeedClarification("Which dates? Please use the format YYYY-MM-DD, e.g. 2026-10-01.")
    if start > end:
        raise NeedClarification(f"The start date {start} is after the end date {end}. Which range do you mean?")
    return start, end



_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|exec|execute|truncate|grant|"
    r"revoke|deny|backup|restore|shutdown|into|waitfor|dbcc|openrowset|"
    r"opendatasource|openquery|xp_\w+|sp_\w+)\b", re.IGNORECASE)


def check_select_only(sql):
    """Allow only a single SELECT (or WITH ... SELECT). Returns cleaned SQL
    or raises ValueError with the reason."""
    s = re.sub(r"--[^\n]*", " ", sql)
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.S)
    s = s.strip().rstrip(";").strip()
    if not re.match(r"^(select|with)\b", s, re.IGNORECASE):
        raise ValueError("Only SELECT queries are allowed.")
    if ";" in s:
        raise ValueError("Only one statement is allowed (no ';').")
    bad = _FORBIDDEN.search(s)
    if bad:
        raise ValueError(f"'{bad.group(0)}' is not allowed - this server is read-only.")
    return s
