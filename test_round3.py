import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

BASE = "http://127.0.0.1:8765"


def post(path, data=None, headers=None):
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(data).encode("utf-8") if data is not None else None,
        headers=hdrs,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def get(path):
    req = urllib.request.Request(BASE + path, method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def wait_task(task_id, max_sec=15):
    deadline = time.time() + max_sec
    last = None
    while time.time() < deadline:
        _, r = get(f"/api/v1/tasks/{task_id}")
        st = r.get("status")
        if st in ("completed", "failed"):
            return r
        last = r
        time.sleep(0.5)
    return last


print("=" * 60)
print("TEST 1: 普通不存在 http 地址 -> 稳定 failed 带失败原因")
print("=" * 60)
_, r = post("/api/v1/tasks", {
    "recording_url": "https://unknown-xyz-2025.com/recordings/call_999.wav",
    "agent_id": "agent_test_fail",
    "call_type": "outbound",
    "call_id": "fail_001",
})
tid1 = r["task_id"]
print(f"  task_id={tid1}")
r = wait_task(tid1)
print(f"  status={r.get('status')}  error={r.get('error_message')}")
assert r["status"] == "failed", f"期望 failed，实际 {r['status']}"
assert "Connection refused" in (r.get("error_message") or ""), f"期望含 Connection refused，实际 {r.get('error_message')}"
print("  ✅ PASS")

print()
print("=" * 60)
print("TEST 2: 带 callback_url 任务 -> 完成后可查回调记录")
print("=" * 60)
_, r = post("/api/v1/tasks", {
    "recording_url": "https://mock-cdn.example.com/records/allrisk_compliance.wav",
    "agent_id": "agent_1001",
    "call_type": "outbound",
    "call_id": "CALL_CB_001",
    "customer_id": "CUS_888",
    "callback_url": "https://mock-callback.example.com/webhook/compliance",
})
tid2 = r["task_id"]
print(f"  task_id={tid2}")
r = wait_task(tid2)
print(f"  status={r.get('status')}")
assert r["status"] == "completed"

time.sleep(1.5)

_, cb = get(f"/api/v1/tasks/{tid2}/callbacks")
print(f"  callback total={cb['total']}  success={cb['success_count']}  failed={cb['failed_count']}")
for item in cb["items"]:
    print(f"    [{item['id']} attempt={item['attempt']} status={item['status']} http={item['http_status_code']} duration={item['duration_ms']}ms err={item.get('error_message')}")
assert cb["total"] >= 1, "至少有一次回调记录"
assert cb["success_count"] >= 1, "至少有一次回调成功"
print("  ✅ PASS")

print()
print("=" * 60)
print("TEST 3: 风险打处理结论 + 按结论筛选")
print("=" * 60)
_, risks = get(f"/api/v1/tasks/{tid2}/risks")
print(f"  total_risks={risks['total_risks']}  unhandled={risks['unhandled_count']}")
for rf in risks["risks"]:
    print(f"    seg={rf['segment_index']} cat={rf['risk_category']} level={rf['risk_level']} text={rf['original_text'][:40]} conclusion={rf['conclusion']}")

segs = [rf["segment_index"] for rf in risks["risks"]]
assert len(segs), "至少有一条风险"
seg_target = segs[0]

_, r = post(
    f"/api/v1/tasks/{tid2}/risks/{seg_target}/conclusion",
    {
        "conclusion": "confirmed_violation",
        "reviewer": "compliance_officer_Li",
        "review_note": "确实辱骂客户，已登记处罚",
    },
)
print(f"  打确认违规 (seg={seg_target}) -> total={r['total_risks']} confirmed={r['confirmed_count']} unhandled={r['unhandled_count']}")
assert r["confirmed_count"] >= 1
assert r["unhandled_count"] == risks["total_risks"] - 1

if len(segs) >= 2:
    seg2 = segs[1]
    _, r = post(
        f"/api/v1/tasks/{tid2}/risks/{seg2}/conclusion",
        {
            "conclusion": "false_alarm",
            "reviewer": "compliance_officer_Li",
            "review_note": "上下文正常，误报",
        },
    )
    print(f"  打误报 (seg={seg2}) -> confirmed={r['confirmed_count']} false={r['false_alarm_count']}")

_, r_unh = get(f"/api/v1/tasks/{tid2}/risks?conclusion=unhandled")
print(f"  按 conclusion=unhandled 筛选 -> total_risks={r_unh['total_risks']}")
_, r_cf = get(f"/api/v1/tasks/{tid2}/risks?conclusion=confirmed_violation")
print(f"  按 conclusion=confirmed_violation 筛选 -> total_risks={r_cf['total_risks']}")
assert r_cf["total_risks"] >= 1
for rf in r_cf["risks"]:
    assert rf["conclusion"] == "confirmed_violation"
print("  ✅ PASS")

print()
print("=" * 60)
print("TEST 4: 任务列表按 call_type + 时间范围过滤")
print("=" * 60)

# 再提交一个 inbound 的任务作为对照组
_, r = post("/api/v1/tasks", {
    "recording_url": "https://oss-cn-hangzhou.aliyuncs.com/records/inbound_normal.wav",
    "agent_id": "agent_1001",
    "call_type": "inbound",
    "call_id": "CALL_INB_001",
})
print(f"  inbound task_id={r['task_id']}")

time.sleep(2.0)  # 等两个任务都处理完

_, lst_all = get("/api/v1/tasks?agent_id=agent_1001")
print(f"  全部 agent_1001 -> total={lst_all['total']}")

_, lst_out = get("/api/v1/tasks?agent_id=agent_1001&call_type=outbound")
print(f"  agent_1001 + outbound -> total={lst_out['total']}")
for item in lst_out["items"]:
    print(f"    id={item['task_id']} call_type={item['call_type']} last_cb={item.get('last_callback_status')} risk_unhandled={item.get('unhandled_risk_count')}")
assert all(i["call_type"] == "outbound" for i in lst_out["items"]), "outbound 过滤不生效"

_, lst_in = get("/api/v1/tasks?agent_id=agent_1001&call_type=inbound")
print(f"  agent_1001 + inbound -> total={lst_in['total']}")
assert all(i["call_type"] == "inbound" for i in lst_in["items"]), "inbound 过滤不生效"

# 时间范围测试
now = datetime.now()
af = (now - timedelta(minutes=5)).isoformat()
bf = (now + timedelta(minutes=1)).isoformat()
_, lst_range = get(f"/api/v1/tasks?agent_id=agent_1001&submitted_after={urllib.parse.quote(af)}&submitted_before={urllib.parse.quote(bf)}")
print(f"  agent_1001 最近5分钟 -> total={lst_range['total']}")
assert lst_range["total"] >= 2, "时间范围过滤应该命中 >= 2"

print("  ✅ PASS")

print()
print("=" * 60)
print("TEST 5: 回调失败域名（域名含 fail 关键词，应重试 3 次，历史可见）")

print("=" * 60)
_, r = post("/api/v1/tasks", {
    "recording_url": "https://mock-cdn.example.com/records/normal02.wav",
    "agent_id": "agent_cb_fail",
    "call_type": "callback",
    "call_id": "CALL_CB_FAIL_001",
    "callback_url": "https://fail-server.example.com/webhook/endpoint",
})
tid5 = r["task_id"]
wait_task(tid5)
print(f"  task_id={tid5}")
time.sleep(6.0)  # 3 次重试 + 间隔

_, cb = get(f"/api/v1/tasks/{tid5}/callbacks")
print(f"  callback total={cb['total']}  success={cb['success_count']}  failed={cb['failed_count']}")
for item in cb["items"]:
    print(f"    [{item['id']}] attempt={item['attempt']} status={item['status']} http={item.get('http_status_code')} err={item.get('error_message')}")
assert cb["total"] == 3, f"期望 3 次，实际 {cb['total']}"
assert cb["failed_count"] == 3, f"期望 3 次失败，实际 {cb['failed_count']}"
print("  ✅ PASS")

print()
print("=" * 60)
print("ALL TESTS PASSED 🎉")
print("=" * 60)
