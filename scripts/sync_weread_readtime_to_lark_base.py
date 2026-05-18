#!/usr/bin/env python3

import argparse
import datetime as dt
import json
import os
import re
import ssl
import subprocess
import sys
import time
from pathlib import Path
from urllib import parse, request


WEREAD_URL = "https://i.weread.qq.com/api/agent/gateway"
SSL_CTX = ssl._create_unverified_context()
TABLE_ID_PATTERN = re.compile(r"^tbl[a-zA-Z0-9]+$")
SKILL_ROOT = Path(__file__).resolve().parents[1]
INIT_BASE_TEMPLATE_PATH = SKILL_ROOT / "assets" / "init_base_template.json"


def eprint(*args):
    print(*args, file=sys.stderr)


def run_cmd(argv):
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"command failed: {' '.join(argv)}")
    return proc.stdout


def lark_json(argv):
    out = run_cmd(argv)
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"failed to parse lark-cli output as JSON: {exc}\nraw: {out[:500]}")
    if not data.get("ok"):
        raise RuntimeError(json.dumps(data, ensure_ascii=False))
    return data["data"]


def load_init_base_template():
    try:
        with INIT_BASE_TEMPLATE_PATH.open("r", encoding="utf-8") as fp:
            template = json.load(fp)
    except FileNotFoundError as exc:
        raise RuntimeError(f"init-base template file not found: {INIT_BASE_TEMPLATE_PATH}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"failed to parse init-base template JSON: {INIT_BASE_TEMPLATE_PATH}: {exc}") from exc

    fields = template.get("readtime_fields")
    blocks = template.get("dashboard_blocks")
    readtime_view_sort = template.get("readtime_view_sort")
    if not isinstance(fields, list) or not fields:
        raise RuntimeError(f"invalid init-base template: readtime_fields must be a non-empty list: {INIT_BASE_TEMPLATE_PATH}")
    if not isinstance(blocks, list):
        raise RuntimeError(f"invalid init-base template: dashboard_blocks must be a list: {INIT_BASE_TEMPLATE_PATH}")
    if readtime_view_sort is not None and not isinstance(readtime_view_sort, dict):
        raise RuntimeError(f"invalid init-base template: readtime_view_sort must be an object: {INIT_BASE_TEMPLATE_PATH}")
    return template


def five_years_ago(day):
    try:
        return day.replace(year=day.year - 5)
    except ValueError:
        return day.replace(year=day.year - 5, month=2, day=28)


def weread_call(payload):
    api_key = os.environ.get("WEREAD_API_KEY")
    if not api_key:
        raise RuntimeError("missing WEREAD_API_KEY environment variable")

    body = dict(payload)
    body.setdefault("skill_version", "1.3.2")
    data = json.dumps(body).encode("utf-8")
    req = request.Request(
        WEREAD_URL,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if "upgrade_info" in result:
        raise RuntimeError(f"WeRead skill needs upgrade: {json.dumps(result['upgrade_info'], ensure_ascii=False)}")
    if result.get("errcode") not in (None, 0):
        raise RuntimeError(f"WeRead API error: {json.dumps(result, ensure_ascii=False)}")
    return result


def parse_date(value):
    return dt.datetime.strptime(value, "%Y-%m-%d").date()


def first_day_of_month(day):
    return day.replace(day=1)


def next_month(day):
    if day.month == 12:
        return day.replace(year=day.year + 1, month=1, day=1)
    return day.replace(month=day.month + 1, day=1)


def iterate_months(start_date, end_date):
    cur = first_day_of_month(start_date)
    last = first_day_of_month(end_date)
    while cur <= last:
        yield cur
        cur = next_month(cur)


def month_base_timestamp(day):
    return int(dt.datetime(day.year, day.month, 1, 0, 0, 0).timestamp())


def format_date_cell(day):
    return f"{day.isoformat()} 00:00:00"


def display_date(day):
    return day.strftime("%Y/%m/%d")


def round_minutes(seconds):
    return float(f"{seconds / 60:.1f}")


def round_hours(seconds):
    return float(f"{seconds / 3600:.2f}")


def daterange(start_date, end_date):
    cur = start_date
    while cur <= end_date:
        yield cur
        cur += dt.timedelta(days=1)


def chunked(items, size):
    for idx in range(0, len(items), size):
        yield items[idx: idx + size]


def extract_range_readtimes(start_date, end_date):
    daily = {}
    for month_start in iterate_months(start_date, end_date):
        resp = weread_call({
            "api_name": "/readdata/detail",
            "mode": "monthly",
            "baseTime": month_base_timestamp(month_start),
        })
        for ts_str, seconds in resp.get("readTimes", {}).items():
            bucket_day = dt.datetime.fromtimestamp(int(ts_str)).date()
            if start_date <= bucket_day <= end_date:
                daily[bucket_day] = int(seconds)

    rows = []
    for day in daterange(start_date, end_date):
        seconds = int(daily.get(day, 0))
        rows.append({
            "date": day,
            "日期": format_date_cell(day),
            "当日阅读时长（秒）": seconds,
            "当日阅读时长（分）": round_minutes(seconds),
            "当日阅读时长（时）": round_hours(seconds),
        })
    return rows


def parse_table_url(url):
    parsed = parse.urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2 or parts[0] != "base":
        raise RuntimeError(f"unsupported base url: {url}")
    base_token = parts[1]
    query = parse.parse_qs(parsed.query)
    table_id = query.get("table", [None])[0]
    if not table_id:
        raise RuntimeError(f"table id not found in url: {url}")
    return base_token, table_id


def is_valid_table_id(table_id):
    return bool(table_id and TABLE_ID_PATTERN.match(table_id))


def fetch_fields(base_token, table_id, identity):
    data = lark_json([
        "lark-cli", "base", "+field-list",
        "--base-token", base_token,
        "--table-id", table_id,
        "--offset", "0",
        "--limit", "100",
        "--as", identity,
    ])
    return data.get("fields", [])


def list_tables(base_token, identity):
    data = lark_json([
        "lark-cli", "base", "+table-list",
        "--base-token", base_token,
        "--offset", "0",
        "--limit", "100",
        "--as", identity,
    ])
    raw_tables = data.get("tables") or data.get("items") or []
    tables = []
    for item in raw_tables:
        tables.append({
            "table_id": item.get("table_id") or item.get("id"),
            "name": item.get("table_name") or item.get("name"),
        })
    return tables


def list_views(base_token, table_id, identity):
    data = lark_json([
        "lark-cli", "base", "+view-list",
        "--base-token", base_token,
        "--table-id", table_id,
        "--offset", "0",
        "--limit", "100",
        "--as", identity,
    ])
    raw_views = data.get("views") or data.get("items") or []
    views = []
    for item in raw_views:
        views.append({
            "view_id": item.get("view_id") or item.get("id"),
            "name": item.get("view_name") or item.get("name"),
            "type": item.get("view_type") or item.get("type"),
        })
    return views


def validate_fields(fields):
    required = {
        "日期": "datetime",
        "当日阅读时长（秒）": "number",
        "当日阅读时长（分）": "number",
        "当日阅读时长（时）": "number",
    }
    indexed = {item["name"]: item for item in fields}
    missing = [name for name in required if name not in indexed]
    if missing:
        raise RuntimeError(f"missing required fields: {', '.join(missing)}")
    wrong = []
    for name, expected_type in required.items():
        actual = indexed[name].get("type")
        if actual != expected_type:
            wrong.append(f"{name}: expected {expected_type}, got {actual}")
    if wrong:
        raise RuntimeError("field type mismatch: " + "; ".join(wrong))


def try_validate_table(base_token, table_id, identity):
    try:
        fields = fetch_fields(base_token, table_id, identity)
        validate_fields(fields)
        return True
    except Exception:  # noqa: BLE001
        return False


def find_matching_readtime_table(base_token, identity):
    matches = []
    for table in list_tables(base_token, identity):
        if not table["table_id"]:
            continue
        if try_validate_table(base_token, table["table_id"], identity):
            matches.append(table)
    if not matches:
        raise RuntimeError("provided table_id format is invalid, and no table with required readtime headers was found in the Base")
    preferred = next((item for item in matches if item.get("name") == "阅读时长"), matches[0])
    return preferred, matches


def resolve_table_for_base(base_token, table_id, identity):
    if is_valid_table_id(table_id):
        return table_id, None
    selected, matches = find_matching_readtime_table(base_token, identity)
    return selected["table_id"], {
        "requested_table_id": table_id,
        "resolved_table_id": selected["table_id"],
        "resolved_table_name": selected.get("name"),
        "reason": "invalid_table_id_format_fallback_to_matching_table",
        "candidate_table_ids": [item["table_id"] for item in matches],
    }


def fetch_existing_records(base_token, table_id, identity):
    offset = 0
    existing = {}
    while True:
        data = lark_json([
            "lark-cli", "base", "+record-list",
            "--base-token", base_token,
            "--table-id", table_id,
            "--field-id", "日期",
            "--field-id", "当日阅读时长（秒）",
            "--field-id", "当日阅读时长（分）",
            "--field-id", "当日阅读时长（时）",
            "--limit", "200",
            "--offset", str(offset),
            "--format", "json",
            "--as", identity,
        ])
        rows = data.get("data", [])
        record_ids = data.get("record_id_list", [])
        for record_id, row in zip(record_ids, rows):
            if not row:
                continue
            date_text = str(row[0])
            date_key = date_text[:10]
            existing[date_key] = {
                "record_id": record_id,
                "日期": date_text,
                "当日阅读时长（秒）": int(row[1]) if row[1] is not None else 0,
                "当日阅读时长（分）": float(row[2]) if row[2] is not None else 0.0,
                "当日阅读时长（时）": float(row[3]) if row[3] is not None else 0.0,
            }
        if not data.get("has_more"):
            break
        offset += len(rows)
    return existing


def payload_for_row(row):
    return {
        "日期": row["日期"],
        "当日阅读时长（秒）": row["当日阅读时长（秒）"],
        "当日阅读时长（分）": row["当日阅读时长（分）"],
        "当日阅读时长（时）": row["当日阅读时长（时）"],
    }


def row_changed(existing, target):
    return not (
        existing["日期"] == target["日期"]
        and int(existing["当日阅读时长（秒）"]) == int(target["当日阅读时长（秒）"])
        and float(existing["当日阅读时长（分）"]) == float(target["当日阅读时长（分）"])
        and float(existing["当日阅读时长（时）"]) == float(target["当日阅读时长（时）"])
    )


def upsert_rows(base_token, table_id, identity, rows, existing, dry_run=False):
    summary = {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "created_record_ids": [],
        "updated_record_ids": [],
    }
    rows_to_create = []
    for row in rows:
        key = row["date"].isoformat()
        payload = payload_for_row(row)
        if key in existing:
            if not row_changed(existing[key], row):
                summary["skipped"] += 1
                continue
            if dry_run:
                summary["updated"] += 1
                continue
            data = lark_json([
                "lark-cli", "base", "+record-upsert",
                "--base-token", base_token,
                "--table-id", table_id,
                "--record-id", existing[key]["record_id"],
                "--json", json.dumps(payload, ensure_ascii=False),
                "--as", identity,
            ])
            record = data.get("record") or {}
            if record.get("record_id"):
                summary["updated_record_ids"].append(record["record_id"])
            summary["updated"] += 1
            time.sleep(0.2)
        else:
            rows_to_create.append(row)

    if dry_run:
        summary["created"] += len(rows_to_create)
        return summary

    for batch in chunked(rows_to_create, 200):
        payload = {
            "fields": ["日期", "当日阅读时长（秒）", "当日阅读时长（分）", "当日阅读时长（时）"],
            "rows": [
                [
                    row["日期"],
                    row["当日阅读时长（秒）"],
                    row["当日阅读时长（分）"],
                    row["当日阅读时长（时）"],
                ]
                for row in batch
            ],
        }
        data = lark_json([
            "lark-cli", "base", "+record-batch-create",
            "--base-token", base_token,
            "--table-id", table_id,
            "--json", json.dumps(payload, ensure_ascii=False),
            "--as", identity,
        ])
        summary["created"] += len(batch)
        summary["created_record_ids"].extend(data.get("record_id_list") or [])
        time.sleep(0.5)
    return summary


def extract_base_meta(data):
    base = data.get("base") or data
    return {
        "name": base.get("name"),
        "base_token": base.get("base_token") or base.get("app_token") or base.get("token"),
        "url": base.get("url"),
        "permission_grant": data.get("permission_grant"),
    }


def extract_table_meta(data):
    table = data.get("table") or data
    return {
        "name": table.get("name"),
        "table_id": table.get("table_id") or table.get("id"),
    }


def create_base(identity, base_name, folder_token=None, time_zone=None):
    argv = ["lark-cli", "base", "+base-create", "--name", base_name, "--as", identity]
    if folder_token:
        argv.extend(["--folder-token", folder_token])
    if time_zone:
        argv.extend(["--time-zone", time_zone])
    return extract_base_meta(lark_json(argv))


def create_table(base_token, identity, name, fields):
    data = lark_json([
        "lark-cli", "base", "+table-create",
        "--base-token", base_token,
        "--name", name,
        "--fields", json.dumps(fields, ensure_ascii=False),
        "--as", identity,
    ])
    return extract_table_meta(data)


def resolve_sortable_readtime_view(base_token, table_id, identity, retries=5, delay_seconds=0.5):
    last_views = []
    for _ in range(retries):
        views = list_views(base_token, table_id, identity)
        last_views = views
        if views:
            grid_view = next((item for item in views if item.get("type") == "grid" and item.get("name") in ("默认视图", "Default View")), None)
            if grid_view:
                return grid_view
            any_grid = next((item for item in views if item.get("type") == "grid"), None)
            if any_grid:
                return any_grid
            any_view = next((item for item in views if item.get("view_id")), None)
            if any_view:
                return any_view
        time.sleep(delay_seconds)
    raise RuntimeError(f"failed to resolve sortable view for table {table_id}, available views: {json.dumps(last_views, ensure_ascii=False)}")


def set_view_sort(base_token, table_id, view_id, identity, sort_config):
    data = lark_json([
        "lark-cli", "base", "+view-set-sort",
        "--base-token", base_token,
        "--table-id", table_id,
        "--view-id", view_id,
        "--json", json.dumps({"sort_config": sort_config}, ensure_ascii=False),
        "--as", identity,
    ])
    return data.get("sort_config") or sort_config


def apply_readtime_table_sort(base_token, table_id, identity, template):
    sort_template = (template.get("readtime_view_sort") or {}).get("sort_config") or []
    if not sort_template:
        return None
    view = resolve_sortable_readtime_view(base_token, table_id, identity)
    applied_sort = set_view_sort(base_token, table_id, view["view_id"], identity, sort_template)
    return {
        "view_id": view["view_id"],
        "view_name": view.get("name"),
        "view_type": view.get("type"),
        "sort_config": applied_sort,
    }


def create_dashboard(base_token, identity, name):
    data = lark_json([
        "lark-cli", "base", "+dashboard-create",
        "--base-token", base_token,
        "--name", name,
        "--as", identity,
    ])
    return {
        "dashboard_id": data.get("dashboard_id") or (data.get("dashboard") or {}).get("dashboard_id"),
        "name": data.get("name") or (data.get("dashboard") or {}).get("name") or name,
    }


def create_dashboard_block(base_token, dashboard_id, identity, block):
    argv = [
        "lark-cli", "base", "+dashboard-block-create",
        "--base-token", base_token,
        "--dashboard-id", dashboard_id,
        "--name", block["name"],
        "--type", block["type"],
        "--data-config", json.dumps(block["data_config"], ensure_ascii=False),
        "--as", identity,
    ]
    if block.get("no_validate"):
        argv.append("--no-validate")
    data = lark_json(argv)
    created = data.get("block") or {}
    return {
        "name": created.get("name") or block["name"],
        "type": created.get("type") or block["type"],
        "block_id": created.get("block_id"),
    }


def build_dashboard_blocks(template):
    return list(template.get("dashboard_blocks") or [])


def initialize_base_scaffold(identity, base_name, folder_token=None, time_zone=None):
    template = load_init_base_template()
    base_meta = create_base(identity, base_name, folder_token=folder_token, time_zone=time_zone)
    if not base_meta["base_token"]:
        raise RuntimeError("failed to parse base_token from base-create result")

    readtime_table = create_table(base_meta["base_token"], identity, "阅读时长", template["readtime_fields"])
    readtime_default_view = apply_readtime_table_sort(base_meta["base_token"], readtime_table["table_id"], identity, template)
    dashboard = create_dashboard(base_meta["base_token"], identity, "微信读书概览")

    blocks = []
    warnings = []
    if dashboard["dashboard_id"]:
        for block in build_dashboard_blocks(template):
            try:
                blocks.append(create_dashboard_block(base_meta["base_token"], dashboard["dashboard_id"], identity, block))
            except Exception as exc:  # noqa: BLE001
                warnings.append({
                    "name": block["name"],
                    "type": block["type"],
                    "error": str(exc),
                })
            time.sleep(0.2)

    return {
        "base": base_meta,
        "tables": {
            "阅读时长": readtime_table,
        },
        "table_views": {
            "阅读时长": {
                "default_view": readtime_default_view,
            },
        },
        "dashboard": {
            **dashboard,
            "blocks": blocks,
        },
        "warnings": warnings,
    }


def sanitize_scaffold_for_output(scaffold):
    if not scaffold:
        return None
    sanitized = json.loads(json.dumps(scaffold, ensure_ascii=False))
    base_meta = sanitized.get("base") or {}
    base_meta.pop("base_token", None)
    return sanitized


def rows_for_output(rows):
    return [
        {
            "日期": display_date(row["date"]),
            "当日阅读时长（秒）": row["当日阅读时长（秒）"],
            "当日阅读时长（分）": row["当日阅读时长（分）"],
            "当日阅读时长（时）": row["当日阅读时长（时）"],
        }
        for row in rows
    ]


def rows_for_sync(rows):
    return [row for row in rows if int(row["当日阅读时长（秒）"]) > 0]


def rows_to_markdown(rows):
    lines = [
        "| 日期 | 当日阅读时长（秒） | 当日阅读时长（分） | 当日阅读时长（时） |",
        "| --- | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {display_date(row['date'])} | {row['当日阅读时长（秒）']} | {row['当日阅读时长（分）']} | {row['当日阅读时长（时）']} |"
        )
    return "\n".join(lines)


def resolve_target(args):
    scaffold = None
    table_resolution = None
    if args.print_only:
        return None, None, scaffold, table_resolution
    if args.table_url:
        base_token, table_id = parse_table_url(args.table_url)
        resolved_table_id, table_resolution = resolve_table_for_base(base_token, table_id, args.identity)
        return base_token, resolved_table_id, scaffold, table_resolution
    if args.base_token and args.table_id:
        resolved_table_id, table_resolution = resolve_table_for_base(args.base_token, args.table_id, args.identity)
        return args.base_token, resolved_table_id, scaffold, table_resolution
    if args.init_base:
        scaffold = initialize_base_scaffold(
            args.identity,
            args.base_name,
            folder_token=args.folder_token,
            time_zone=args.time_zone,
        )
        readtime_table = scaffold["tables"]["阅读时长"]
        return scaffold["base"]["base_token"], readtime_table["table_id"], scaffold, table_resolution
    raise RuntimeError("provide --table-url, or both --base-token and --table-id, or use --init-base")


def main():
    parser = argparse.ArgumentParser(description="Read WeRead daily readtime and optionally sync it into Lark Base.")
    parser.add_argument("--table-url", help="Lark Base URL containing base token and table id")
    parser.add_argument("--base-token")
    parser.add_argument("--table-id")
    parser.add_argument("--start-date", help="YYYY-MM-DD, default five years ago")
    parser.add_argument("--end-date", help="YYYY-MM-DD, default today")
    parser.add_argument("--as", dest="identity", default="user", choices=["user", "bot"])
    parser.add_argument("--dry-run", action="store_true", help="compute sync result but do not write records")
    parser.add_argument("--print-only", action="store_true", help="only read and print markdown table; skip all Base operations")
    parser.add_argument("--init-base", action="store_true", help="create a new Base scaffold and sync into its 阅读时长 table")
    parser.add_argument("--base-name", default="微信读书书架", help="Base name used with --init-base")
    parser.add_argument("--folder-token", help="optional folder token for --init-base")
    parser.add_argument("--time-zone", help="optional timezone for --init-base")
    args = parser.parse_args()

    if args.print_only and args.init_base:
        raise RuntimeError("--print-only and --init-base cannot be used together")
    if args.dry_run and args.init_base:
        raise RuntimeError("--dry-run cannot be used together with --init-base")

    today = dt.date.today()
    start_date = parse_date(args.start_date) if args.start_date else five_years_ago(today)
    end_date = parse_date(args.end_date) if args.end_date else today
    if start_date > end_date:
        raise RuntimeError("start-date cannot be after end-date")

    target_rows = extract_range_readtimes(start_date, end_date)
    sync_rows = rows_for_sync(target_rows)
    markdown_table = rows_to_markdown(target_rows)

    base_token, table_id, scaffold, table_resolution = resolve_target(args)

    summary = {"created": 0, "updated": 0, "skipped": 0, "created_record_ids": [], "updated_record_ids": []}
    mode = "print_only" if args.print_only else "sync"
    if not args.print_only:
        fields = fetch_fields(base_token, table_id, args.identity)
        validate_fields(fields)
        existing = fetch_existing_records(base_token, table_id, args.identity)
        summary = upsert_rows(base_token, table_id, args.identity, sync_rows, existing, dry_run=args.dry_run)
        mode = "dry_run" if args.dry_run else "sync"

    output = {
        "mode": mode,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "days": len(target_rows),
        "sync_days": len(sync_rows),
        "dry_run": args.dry_run,
        "print_only": args.print_only,
        "table_id": table_id,
        "markdown_table": markdown_table,
        **summary,
        "rows": rows_for_output(target_rows),
    }
    if scaffold:
        output["initialized_base"] = sanitize_scaffold_for_output(scaffold)
    if table_resolution:
        output["table_resolution"] = table_resolution
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        eprint(f"ERROR: {exc}")
        sys.exit(1)
