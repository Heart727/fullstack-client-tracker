# -*- coding: utf-8 -*-
"""verify_api.py —— 后端接口自检脚本（用 Python 自带库，无需额外依赖）

⚠️ 重要：本脚本会清空并写入测试数据，必须指向【独立的测试数据库】运行，
   绝不能对着存了真实客户数据的库跑（会把数据抹掉）！

推荐用法（用环境变量把数据库指到临时文件，测试库和真实库彻底隔离）：
    先在一个终端启动测试实例：
        DATABASE_PATH=test_clients.db .venv\\Scripts\\python -m uvicorn main:app --port 8000
    再另开一个终端运行：
        .venv\\Scripts\\python verify_api.py
    测完把 test_clients.db 删掉即可（Windows PowerShell: Remove-Item test_clients.db）

脚本按顺序把 增/查/筛选/改/统计/删/错误处理 全部真实调用一遍，
每项输出 PASS 或 FAIL，最后给总结论。退出码：全部通过为 0，否则为 1。
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

BASE = "http://localhost:8000"
TODAY = date.today().strftime("%Y-%m-%d")

passed, failed = [], []  # 收集每项结果，最后统一汇报


def check(name, condition, detail=""):
    """记录一项检查结果：condition 为真记 PASS，否则记 FAIL。"""
    if condition:
        passed.append(name)
        print(f"  ✅ PASS  {name}")
    else:
        failed.append(name)
        print(f"  ❌ FAIL  {name}  {detail}")


def call(method, path, body=None):
    """用标准库 urllib 调接口，返回 (HTTP状态码, 解析后的JSON)。

    手写 urllib 而不是用 requests，是为了这个脚本零依赖、任何环境直接跑。
    注意：URL 里的中文（如筛选条件"待跟进"）必须先 quote 编码成
    百分号形式，否则 HTTP 请求行只允许 ASCII 字符，会直接抛异常。
    """
    if "?" in path:
        base, query = path.split("?", 1)
        path = base + "?" + urllib.parse.quote(query, safe="=&")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        # 4xx/5xx 也是"正常返回"：错误处理测试要的就是这个
        return err.code, json.loads(err.read().decode("utf-8"))


def clear_all():
    """清空现有客户，保证脚本可重复运行、结果可预期。"""
    _, clients = call("GET", "/api/clients")
    for c in clients:
        call("DELETE", f"/api/clients/{c['id']}")


def main():
    print(f"开始自检（目标服务 {BASE}，今天 = {TODAY}）\n")

    clear_all()  # 先清场：重复运行也能得到一样的结果

    # ---------- 1. 新增 ----------
    print("【1】新增客户")
    code, c1 = call("POST", "/api/clients", {
        "name": "张三", "source": "Upwork", "project_desc": "给餐厅做点餐网页",
        "quote": 3000, "status": "待跟进", "next_followup": TODAY,
        "notes": "回复很快，预算充足",
    })
    check("新建客户返回 201", code == 201, f"实际 {code}")
    check("新客户自动分配了 id", isinstance(c1.get("id"), int), str(c1))

    code, c2 = call("POST", "/api/clients", {
        "name": "李四", "source": "朋友介绍", "status": "进行中",
        "quote": "", "next_followup": "2026-08-25",
    })
    check("第二个客户（报价留空）返回 201", code == 201, f"实际 {code}")
    check("留空报价存成了 null", c2.get("quote") is None, str(c2.get("quote")))

    code, c3 = call("POST", "/api/clients", {
        "name": "王五", "source": "Upwork", "status": "已完成",
        "next_followup": "2026-08-01",  # 过去的日期：不该出现在"今日要跟进"里
    })
    check("第三个客户（已完成）返回 201", code == 201, f"实际 {code}")

    # ---------- 2. 错误处理（不静默失败） ----------
    print("\n【2】错误处理")
    code, err = call("POST", "/api/clients", {"name": ""})
    check("姓名留空被拒绝（400）", code == 400 and "姓名" in err.get("detail", ""), f"实际 {code} {err}")

    code, err = call("POST", "/api/clients", {"name": "测试", "status": "不存在的状态"})
    check("非法状态被拒绝（400）", code == 400, f"实际 {code} {err}")

    code, err = call("POST", "/api/clients", {"name": "测试", "quote": -5})
    check("负数报价被拒绝（400）", code == 400, f"实际 {code} {err}")

    # 下面三条是"格式错误"：由全局校验处理器统一转成 400 + 中文原因
    code, err = call("POST", "/api/clients", {"name": "测试", "quote": "abc"})
    check("非数字报价被拒绝（400 中文）",
          code == 400 and isinstance(err.get("detail"), str), f"实际 {code} {err}")

    code, err = call("POST", "/api/clients", {"status": "待跟进"})  # 整个 name 字段缺失
    check("缺 name 字段被拒绝（400）", code == 400, f"实际 {code} {err}")

    code, err = call("POST", "/api/clients", {"name": "测试", "next_followup": "2026-8-5"})
    check("日期不补零被拒绝（400）", code == 400, f"实际 {code} {err}")

    code, err = call("POST", "/api/clients", {"name": "测试", "next_followup": "2026-08-18abc"})
    check("日期带垃圾尾巴被拒绝（400）", code == 400, f"实际 {code} {err}")

    code, err = call("GET", "/api/clients/99999")
    check("查不存在的客户返回 404", code == 404, f"实际 {code}")

    # ---------- 3. 查询与筛选 ----------
    print("\n【3】查询与筛选")
    code, all_clients = call("GET", "/api/clients")
    check("列表返回全部客户", len(all_clients) == 3, f"实际 {len(all_clients)} 条")

    code, rows = call("GET", "/api/clients?status=待跟进")
    check("按状态筛选（待跟进=1 条）", len(rows) == 1 and rows[0]["name"] == "张三",
          f"实际 {len(rows)} 条")

    code, rows = call("GET", "/api/clients?source=Upwork")
    check("按来源筛选（Upwork=2 条）", len(rows) == 2, f"实际 {len(rows)} 条")

    code, rows = call("GET", "/api/clients?q=点餐")
    check("关键词搜索（「点餐」命中张三）", len(rows) == 1 and rows[0]["name"] == "张三",
          f"实际 {len(rows)} 条")

    code, rows = call("GET", "/api/clients?status=已完成&source=Upwork")
    check("组合筛选（已完成+Upwork=王五）", len(rows) == 1 and rows[0]["name"] == "王五",
          f"实际 {len(rows)} 条")

    # ---------- 4. 修改 ----------
    print("\n【4】修改客户")
    code, updated = call("PUT", f"/api/clients/{c1['id']}", {
        "name": "张三", "source": "Upwork", "project_desc": "给餐厅做点餐网页（已改需求）",
        "quote": 4500, "status": "进行中", "next_followup": TODAY, "notes": "加急",
    })
    check("修改成功返回 200", code == 200, f"实际 {code}")
    check("报价改成了 4500", updated.get("quote") == 4500, str(updated.get("quote")))
    check("状态改成了进行中", updated.get("status") == "进行中", updated.get("status"))

    code, err = call("PUT", "/api/clients/99999", {"name": "不存在"})
    check("修改不存在的客户返回 404", code == 404, f"实际 {code}")

    # ---------- 5. 首页统计 ----------
    print("\n【5】首页统计")
    code, stats = call("GET", "/api/stats")
    counts = stats.get("counts", {})
    check("待跟进数量 = 0", counts.get("待跟进") == 0, str(counts))   # 张三改成进行中了
    check("进行中数量 = 2", counts.get("进行中") == 2, str(counts))   # 张三 + 李四
    check("已完成数量 = 1", counts.get("已完成") == 1, str(counts))   # 王五

    names = [c["name"] for c in stats.get("today_followups", [])]
    # 张三的跟进日=今天 → 应该出现
    check("今日要跟进含张三（跟进日=今天）", "张三" in names, str(names))
    # 李四的跟进日在未来（08-25）→ 不应该出现
    check("今日要跟进不含李四（跟进日在未来）", "李四" not in names, str(names))
    # 王五已完成，且跟进日已过 → 不应该出现
    check("今日要跟进不含已完成的王五", "王五" not in names, str(names))

    # ---------- 6. 删除 ----------
    print("\n【6】删除客户")
    code, result = call("DELETE", f"/api/clients/{c3['id']}")
    check("删除成功返回 {ok: true}", code == 200 and result.get("ok") is True, f"实际 {code}")
    code, _ = call("DELETE", f"/api/clients/{c3['id']}")
    check("重复删除返回 404", code == 404, f"实际 {code}")

    # ---------- 总结 ----------
    print("\n" + "=" * 50)
    print(f"自检结束：{len(passed)} 通过，{len(failed)} 失败")
    if failed:
        print("失败项：", "、".join(failed))
        sys.exit(1)
    print("全部通过 🎉")
    sys.exit(0)


if __name__ == "__main__":
    main()
