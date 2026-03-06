"""History API: list, get, delete, export."""

from datetime import datetime
from typing import Any, List

from fastapi import APIRouter, HTTPException, Body

from ..core import get_db_conn

router = APIRouter(prefix="/api", tags=["history"])


@router.get("/history")
async def history_list(
    limit: int = 20,
    offset: int = 0,
    date_from: str | None = None,
    date_to: str | None = None,
    lot_number: str | None = None,
    mfg_name: str | None = None,
    inspection_result: str | None = None,
) -> dict:
    conn = get_db_conn()
    cur = conn.cursor()
    where: List[str] = []
    params: List[Any] = []
    if date_from:
        where.append("timestamp >= ?")
        params.append(date_from)
    if date_to:
        where.append("timestamp <= ?")
        params.append(date_to)
    if lot_number:
        where.append("lot_number LIKE ?")
        params.append(f"%{lot_number}%")
    if mfg_name:
        where.append("mfg_name LIKE ?")
        params.append(f"%{mfg_name}%")
    if inspection_result:
        where.append("inspection_result = ?")
        params.append(inspection_result)
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    count_row = cur.execute(f"SELECT COUNT(*) as c FROM inspection_history{where_sql}", params).fetchone()
    total = int(count_row["c"] if count_row else 0)
    rows = cur.execute(
        f"SELECT * FROM inspection_history{where_sql} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    data = [
        {
            "id": r["id"],
            "timestamp": r["timestamp"],
            "lot_number": r["lot_number"],
            "mfg_name": r["mfg_name"],
            "mfg_part_number": r["mfg_part_number"],
            "material": r["material"],
            "ball_diameter": r["ball_diameter"],
            "ball_diameter_mm": r["ball_diameter_mm"],
            "customer_name": r["customer_name"],
            "inspection_result": r["inspection_result"],
            "total_balls": r.get("total_balls"),
            "good_balls": r.get("good_balls"),
            "bad_balls": r.get("bad_balls"),
            "no_balls": r.get("no_balls"),
            "composite_image_path": r["composite_image_path"],
        }
        for r in rows
    ]
    stats_row = cur.execute(
        "SELECT COUNT(*) as total_cycles, SUM(good_balls) as good_balls, SUM(bad_balls) as bad_balls, SUM(no_balls) as no_balls FROM inspection_history"
    ).fetchone()
    statistics = {
        "total_cycles": int(stats_row["total_cycles"] or 0),
        "good_balls": int(stats_row["good_balls"] or 0),
        "bad_balls": int(stats_row["bad_balls"] or 0),
        "no_balls": int(stats_row["no_balls"] or 0),
    }
    return {"success": True, "data": data, "statistics": statistics, "pagination": {"total": total, "limit": limit, "offset": offset}}


@router.get("/history/{cycle_id}")
async def history_get(cycle_id: int) -> dict:
    conn = get_db_conn()
    cur = conn.cursor()
    row = cur.execute("SELECT * FROM inspection_history WHERE id = ?", (cycle_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Cycle not found")
    record = {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "lot_number": row["lot_number"],
        "mfg_name": row["mfg_name"],
        "mfg_part_number": row["mfg_part_number"],
        "material": row["material"],
        "ball_diameter": row["ball_diameter"],
        "ball_diameter_mm": row["ball_diameter_mm"],
        "customer_name": row["customer_name"],
        "inspection_result": row["inspection_result"],
        "total_balls": row.get("total_balls"),
        "good_balls": row.get("good_balls"),
        "bad_balls": row.get("bad_balls"),
        "no_balls": row.get("no_balls"),
        "composite_image_path": row["composite_image_path"],
    }
    return {"success": True, "data": record}


@router.delete("/history/{cycle_id}")
async def history_delete(cycle_id: int) -> dict:
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM inspection_history WHERE id = ?", (cycle_id,))
    conn.commit()
    return {"success": True}


@router.delete("/history/bulk")
async def history_bulk_delete(ids: List[int] = Body(...)) -> dict:
    if not ids:
        return {"success": True}
    conn = get_db_conn()
    cur = conn.cursor()
    placeholders = ",".join("?" for _ in ids)
    cur.execute(f"DELETE FROM inspection_history WHERE id IN ({placeholders})", ids)
    conn.commit()
    return {"success": True}


@router.post("/history/export")
async def history_export(body: dict) -> dict:
    filters = body.get("filters") or {}
    result = await history_list(
        limit=10000,
        offset=0,
        date_from=filters.get("date_from"),
        date_to=filters.get("date_to"),
        lot_number=filters.get("lot_number"),
        mfg_name=filters.get("mfg_name"),
        inspection_result=filters.get("inspection_result"),
    )
    rows = result.get("data", [])
    headers = ["id", "timestamp", "lot_number", "mfg_name", "mfg_part_number", "material", "ball_diameter", "ball_diameter_mm", "customer_name", "inspection_result"]
    lines = [",".join(headers)]
    for r in rows:
        lines.append(",".join(str(r.get(k, "")) for k in headers))
    content = "\n".join(lines)
    filename = f"history_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return {"success": True, "data": {"content": content, "filename": filename}}
