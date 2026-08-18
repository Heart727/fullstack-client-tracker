# -*- coding: utf-8 -*-
"""database.py —— 数据库层（所有和 SQLite 打交道的事都在这里）

为什么单独一个文件？
main.py 只负责"接待请求、返回结果"，不关心数据存在哪、怎么存。
将来如果想换数据库（比如 MySQL），只需要改这个文件，接口函数不变。

本文件提供的函数（每个函数都只干一件事，名字即用途）：
    init_db()                          建表（第一次运行时自动调用）
    list_clients(status, source, q)    查客户列表（支持三个条件组合筛选）
    create_client(fields)              新增客户，返回完整记录
    update_client(client_id, fields)   修改客户，返回修改后的记录
    delete_client(client_id)           删除客户
    get_stats()                        首页统计（各状态数量 + 今日要跟进）
"""

import os
import sqlite3
from datetime import date, datetime

# 数据库文件位置：默认放在项目目录下（删除这个文件 = 清空所有数据）。
# 但允许用环境变量 DATABASE_PATH 指定别的位置，两个用途：
#   1) 部署：Railway 的硬盘是临时的，重启就清空，必须把数据库放到
#      挂载的持久化卷上（如 DATABASE_PATH=/data/clients.db）；
#   2) 测试：verify_api.py 自检时指向一个临时测试库，绝不碰真实数据。
DB_PATH = os.environ.get("DATABASE_PATH") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "clients.db"
)

# 客户的三种状态（首页统计就是按这三个数数量）
# 以后想加状态（比如"已放弃"），改这个列表 + 前端对应位置即可
STATUSES = ["待跟进", "进行中", "已完成"]


def _connect():
    """打开一个数据库连接。

    每次都新建连接、用完就关（不搞"全局共用一个连接"）：
    FastAPI 会开多个线程同时处理请求，SQLite 连接默认不能跨线程共用，
    一人一条连接最省心，也避免各种奇怪的锁错误。
    """
    conn = sqlite3.connect(DB_PATH)
    # 让查询结果能用字典方式取数：row["name"]，而不是 row[0]
    conn.row_factory = sqlite3.Row
    # 两个并发保护：
    #   busy_timeout = 等锁最多 5 秒。FastAPI 多线程可能同时写库，
    #   没这个设置会立刻报 "database is locked" 变成 500 错误。
    #   WAL 模式 = 读写不互相阻塞（读的快照机制），并发更稳。
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db():
    """建表。用 CREATE TABLE IF NOT EXISTS，重复调用不会出错。

    字段说明（对应需求清单里的 7 个客户字段 + 2 个自动字段）：
        name          姓名（必填，不能空）
        source        来源（Upwork、朋友介绍……自由填）
        project_desc  项目描述
        quote         报价（单位：元，允许小数，也允许不填）
        status        状态：待跟进 / 进行中 / 已完成
        notes         备注
        next_followup 下次跟进日期（格式 YYYY-MM-DD，允许不填）
        created_at    创建时间（自动填，不用管）
        updated_at    最后修改时间（自动更新）
    """
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clients (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL,
                source        TEXT DEFAULT '',
                project_desc  TEXT DEFAULT '',
                quote         REAL,
                status        TEXT NOT NULL DEFAULT '待跟进',
                notes         TEXT DEFAULT '',
                next_followup TEXT DEFAULT '',
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        # finally 保证无论建表成功还是失败，连接都会被关闭（不留后门）
        conn.close()


def _row_to_dict(row):
    """把 sqlite3.Row 转成普通字典，方便 FastAPI 直接转 JSON 返回。"""
    return {key: row[key] for key in row.keys()}


def _now():
    """当前时间字符串，格式 2026-08-18 17:30:00。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _validate(fields):
    """检查要写入的数据合不合法，不合法就抛 ValueError（中文提示）。

    在这里把关，比在接口里逐个判断干净：所有入口共用一套规则。
    """
    # 姓名必填：没有姓名的客户记录毫无意义
    name = (fields.get("name") or "").strip()
    if not name:
        raise ValueError("姓名不能为空")

    # 状态必须在三个合法值里（防止前端或用户乱传状态）
    status = fields.get("status") or "待跟进"
    if status not in STATUSES:
        raise ValueError(f"状态必须是：{'、'.join(STATUSES)}")

    # 报价如果是空字符串就存 NULL（"不知道价格"和"报价 0 元"不是一回事）
    quote = fields.get("quote")
    if quote == "" or quote is None:
        quote = None
    else:
        try:
            quote = float(quote)
        except (TypeError, ValueError):
            raise ValueError("报价必须是数字（单位：元）")
        if quote < 0:
            raise ValueError("报价不能是负数")

    # 日期格式校验：不填可以，填了就必须是严格 YYYY-MM-DD。
    # 为什么用 fromisoformat 而不是 strptime？
    # strptime 太宽容：会接受 "2026-8-5"（不补零）、"2026-08-18abc"（尾部垃圾）
    # 这类脏数据，而统计接口用字符串比较日期，格式不齐会判断错"今天要跟进"。
    followup = (fields.get("next_followup") or "").strip()
    if followup:
        try:
            date.fromisoformat(followup)
        except ValueError:
            raise ValueError("下次跟进日期格式必须是 YYYY-MM-DD")

    return {
        "name": name,
        "source": (fields.get("source") or "").strip(),
        "project_desc": (fields.get("project_desc") or "").strip(),
        "quote": quote,
        "status": status,
        "notes": (fields.get("notes") or "").strip(),
        "next_followup": followup,
    }


def list_clients(status="", source="", q=""):
    """查客户列表，三个筛选条件可以单独用，也可以组合用。

    参数：
        status  只查某个状态（空 = 不限）
        source  只查某个来源（空 = 不限，精确匹配）
        q       关键词（在姓名/项目描述/备注里模糊搜，空 = 不限）

    返回：客户字典列表，最新创建的排前面。
    """
    # 下面所有查询条件都用 ? 占位符 + 参数元组传入——
    # 这叫"参数化查询"，是防 SQL 注入的标准做法：
    # 用户输入的内容永远被当作"数据"而不是"代码"处理，
    # 就算输入里带恶意的 SQL 片段也只会被当成普通文字。
    conditions = []   # 存放各个 WHERE 条件片段
    params = []       # 存放对应的参数值（顺序要和 conditions 一致）

    if status:
        conditions.append("status = ?")
        params.append(status)
    if source:
        conditions.append("source = ?")
        params.append(source)
    if q:
        # LIKE 模糊搜索：% 表示任意字符；三个字段任一命中就算
        conditions.append("(name LIKE ? OR project_desc LIKE ? OR notes LIKE ?)")
        params.extend([f"%{q}%"] * 3)

    sql = "SELECT * FROM clients"
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY id DESC"

    conn = _connect()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_client(client_id):
    """按 id 查单个客户；查不到返回 None（接口层据此返回 404）。"""
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def create_client(fields):
    """新增客户。fields 是要写入的字段字典（前端表单提交的内容）。

    返回：刚创建的完整记录（含自动生成的 id 和创建时间）。
    """
    data = _validate(fields)  # 先过一遍合法性检查，不合法直接抛 ValueError
    conn = _connect()
    try:
        now = _now()
        cursor = conn.execute(
            """
            INSERT INTO clients
                (name, source, project_desc, quote, status, notes,
                 next_followup, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["name"], data["source"], data["project_desc"],
                data["quote"], data["status"], data["notes"],
                data["next_followup"], now, now,
            ),
        )
        conn.commit()
        # lastrowid = SQLite 自动分配的主键，就是新客户的 id
        return get_client(cursor.lastrowid)
    finally:
        conn.close()


def update_client(client_id, fields):
    """修改客户。只更新表单里传过来的字段，没传的字段保持原样。

    先确认这个 id 存在；不存在就抛 LookupError（接口层返回 404）。
    返回：修改后的完整记录。
    """
    data = _validate(fields)

    conn = _connect()
    try:
        # 先查存在性：改一个不存在的客户应当明确报"查无此客户"
        exists = conn.execute(
            "SELECT id FROM clients WHERE id = ?", (client_id,)
        ).fetchone()
        if exists is None:
            raise LookupError(f"客户 id={client_id} 不存在")

        conn.execute(
            """
            UPDATE clients
            SET name = ?, source = ?, project_desc = ?, quote = ?,
                status = ?, notes = ?, next_followup = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                data["name"], data["source"], data["project_desc"],
                data["quote"], data["status"], data["notes"],
                data["next_followup"], _now(), client_id,
            ),
        )
        conn.commit()
        return get_client(client_id)
    finally:
        conn.close()


def delete_client(client_id):
    """删除客户。返回 True 表示删掉了，False 表示 id 不存在。"""
    conn = _connect()
    try:
        cursor = conn.execute("DELETE FROM clients WHERE id = ?", (client_id,))
        conn.commit()
        return cursor.rowcount > 0  # rowcount = 实际删掉了几行
    finally:
        conn.close()


def get_stats():
    """首页统计。

    返回字典：
        counts          各状态各有多少客户
        today_followups 今日要跟进的客户列表
                        （下次跟进日期 ≤ 今天，且还没完成）
    """
    conn = _connect()
    try:
        # GROUP BY status 一条 SQL 数出每个状态的数量
        # 注意：某个状态一条都没有时不会出现在结果里，所以要补 0
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM clients GROUP BY status"
        ).fetchall()
        counts = {s: 0 for s in STATUSES}  # 先全部置 0
        for r in rows:
            # .get 兜底：万一库里存了 STATUSES 之外的旧状态（比如以后
            # 改了状态列表），只把它加进 counts 而不报错
            counts[r["status"]] = counts.get(r["status"], 0) + r["n"]

        # 今日要跟进：next_followup 有值、≤ 今天、且状态不是"已完成"
        today = datetime.now().strftime("%Y-%m-%d")
        follow_rows = conn.execute(
            """
            SELECT * FROM clients
            WHERE next_followup != ''
              AND next_followup <= ?
              AND status != '已完成'
            ORDER BY next_followup ASC, id ASC
            """,
            (today,),
        ).fetchall()

        return {
            "counts": counts,
            "today_followups": [_row_to_dict(r) for r in follow_rows],
        }
    finally:
        conn.close()
