"""Static inventory of the code that WRITES `parts_catalog.base_price` (test_base_price_writers.py, the report generator).

AST based (v2, 2026-11-01). A writer is
  * a SQL string literal that INSERTs INTO / UPDATEs parts_catalog AND names base_price inside the SAME statement;
  * an ORM assignment `x.base_price = ...` or a `base_price=` keyword to PartsCatalog(...), .values(...), update(...);
  * the apply_base_price_rule call sites (indirect writers: they write through the policy rule).
Shell / SQL files under the repo are scanned textually. A *literal formula* is a code line (docstrings and comments
excluded) that computes a price itself: `1.45`, `(1 + MARGIN)`, `* MARGIN`, `cost * MARGIN`."""
import ast
import os
import re

SKIP_DIRS = ("/tests", "/devtests", "/legacy", "/backups", "/uploads", "/alembic", "__pycache__", "/state", "/data", "/node_modules")
STMT_RE = re.compile(r"(INSERT\s+INTO\s+parts_catalog\b|UPDATE\s+parts_catalog\b)", re.I)
FORMULA_RE = re.compile(r"1\.45|\(1 \+ MARGIN\)|\* MARGIN|cost \* MARGIN")
ORM_KW = {"PartsCatalog", "values", "update", "insert"}


def py_files(root):
    for r, _d, fs in os.walk(root):
        rr = r.replace(root, "") + "/"
        if any(x in rr for x in SKIP_DIRS):
            continue
        for f in fs:
            if f.endswith(".py"):
                yield os.path.join(r, f)


def _doc_lines(tree):
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) \
                    and isinstance(first.value.value, str):
                lines.update(range(first.lineno, first.end_lineno + 1))
    return lines


def _strip_comment(st):
    if "#" in st:
        head = st.split("#", 1)[0]
        if head.count('"') % 2 == 0 and head.count("'") % 2 == 0:
            return head
    return st


_INSERT_COLS = re.compile(r"INSERT\s+INTO\s+parts_catalog\s*\((.*?)\)\s*(?:VALUES|SELECT|OVERRIDING)", re.I | re.S)
_ASSIGN = re.compile(r"(?<![\w.])base_price\s*=\s*(?!=)(?!0\s*(?:THEN|\)|AND|OR|\s*$))", re.I)


def _sql_assigns_base_price(sql):
    """True when the statement WRITES base_price: it is in an INSERT's column list, or assigned after SET (also in
    ON CONFLICT ... DO UPDATE SET). A base_price that is only READ (WHERE / SELECT / a CTE) is not a write."""
    if "base_price" not in sql or not STMT_RE.search(sql):
        return False
    m = _INSERT_COLS.search(sql)
    if m and re.search(r"(?<![\w.])base_price\b", m.group(1)):
        return True
    for part in re.split(r"\bSET\b", sql, flags=re.I)[1:]:
        if _ASSIGN.search(part):
            return True
    return False


def scan_file(path):
    src = open(path, errors="ignore").read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    doc = _doc_lines(tree)
    writes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.lineno not in doc:
            if _sql_assigns_base_price(node.value):
                writes.append(("sql", node.lineno))
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Attribute) and t.attr == "base_price" for t in targets):
                writes.append(("orm", node.lineno))
        elif isinstance(node, ast.Call):
            fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if fname in ORM_KW and any(k.arg == "base_price" for k in node.keywords):
                writes.append(("orm", node.lineno))
            if fname == "apply_base_price_rule":
                writes.append(("rule", node.lineno))
    formulas = []
    for i, raw in enumerate(src.splitlines(), 1):
        st = raw.strip()
        if i in doc or not st or st.startswith("#") or st.startswith("--"):
            continue
        code = _strip_comment(st)
        if FORMULA_RE.search(code) and "price_policy" not in code:
            formulas.append(i)
    return {"writes": sorted(set(writes)), "formulas": formulas}


def scan(root):
    """{relative path: {"writes": [(kind, line)], "formulas": [lines]}} for every Python file with a base_price writer."""
    out = {}
    for p in py_files(root):
        r = scan_file(p)
        if r and r["writes"]:
            out[os.path.relpath(p, root)] = r
    return out


def scan_text_files(repo_root):
    """.sql / .sh files that write base_price (textual)."""
    out = {}
    for r, _d, fs in os.walk(repo_root):
        rr = r + "/"
        if any(x in rr for x in ("/node_modules", "/.git/", "/backups", "/archive", "/candidates", "/.claude", "/frontend")):
            continue
        for f in fs:
            if f.endswith((".sql", ".sh")):
                p = os.path.join(r, f)
                txt = open(p, errors="ignore").read()
                if re.search(r"UPDATE\s+parts_catalog[^;]*base_price|INSERT\s+INTO\s+parts_catalog[^;]*base_price", txt, re.I | re.S):
                    out[os.path.relpath(p, repo_root)] = True
    return out
