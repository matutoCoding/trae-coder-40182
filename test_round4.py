import json
import threading
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

BASE = "http://127.0.0.1:8766"

_callback_received = []
_callback_server_port = 9999


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b""
        payload = None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {"_raw": raw.decode("utf-8", errors="replace")}
        headers = {k: v for k, v in self.headers.items()}
        record = {
            "path": self.path,
            "method": "POST",
            "headers": headers,
            "payload": payload,
            "received_at": datetime.now().isoformat(),
        }
        _callback_received.append(record)

        path = self.path
        if path.startswith("/cb/500"):
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"error","detail":"mock server error"}')
        elif path.startswith("/cb/400"):
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"bad_request"}')
        elif path.startswith("/cb/slow"):
            time.sleep(8)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"success":true}')
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"success":true,"server":"mock_callback","ts":"'
                             + datetime.now().isoformat().encode() + b'"}')

    def log_message(self, fmt, *args):
        pass


def _start_callback_server():
    srv = HTTPServer(("127.0.0.1", _callback_server_port), _CallbackHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.3)
    return srv


def post(path, data=None):
    hdrs = {"Content-Type": "application/json"}
    body = json.dumps(data).encode("utf-8") if data is not None else b""
    req = urllib.request.Request(BASE + path, data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8")
            detail = json.loads(body)
        except Exception:
            detail = {"detail": body}
        return e.code, detail


def get(path):
    req = urllib.request.Request(BASE + path, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8")
            detail = json.loads(body)
        except Exception:
            detail = {"detail": body}
        return e.code, detail


def wait_task(task_id, max_sec=20):
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


def today():
    return datetime.now().strftime("%Y-%m-%d")


def main():
    print("Starting local mock callback server on 127.0.0.1:" + str(_callback_server_port))
    srv = _start_callback_server()
    try:
        _run_all()
    finally:
        try:
            srv.shutdown()
        except Exception:
            pass


def _run_all():
    print("=" * 70)
    print("TEST 1: 失败分类细分（地址不可达 / 文件损坏 / ASR 服务异常）")
    print("=" * 70)

    _, r = post("/api/v1/tasks", {
        "recording_url": "https://unknown-xyz-99999.com/test.wav",
        "agent_id": "agent_fail_A",
        "call_type": "outbound",
    })
    tid1 = r["task_id"]
    res1 = wait_task(tid1)
    print(f"  [地址不可达 unknown host] status={res1.get('status')}  failure_type={res1.get('failure_type')}  suggest_retry={res1.get('suggest_retry')}")
    print(f"      failure_reason={res1.get('failure_reason')}")
    assert res1["status"] == "failed"
    assert res1["failure_type"] == "recording_unreachable"
    assert res1["suggest_retry"] is False
    assert isinstance(res1["failure_reason"], str) and len(res1["failure_reason"]) > 5

    _, r = post("/api/v1/tasks", {
        "recording_url": "https://example.com/404/old/deleted_archive.wav",
        "agent_id": "agent_fail_B",
        "call_type": "inbound",
    })
    tid2 = r["task_id"]
    res2 = wait_task(tid2)
    print(f"  [文件 404] status={res2.get('status')}  failure_type={res2.get('failure_type')}  suggest_retry={res2.get('suggest_retry')}")
    print(f"      failure_reason={res2.get('failure_reason')}")
    assert res2["status"] == "failed"
    assert res2["failure_type"] == "recording_corrupted" or res2["failure_type"] == "recording_unreachable"
    assert res2["suggest_retry"] is False

    _, r = post("/api/v1/tasks", {
        "recording_url": "https://503.example.com/records/broken.wav",
        "agent_id": "agent_fail_C",
        "call_type": "callback",
    })
    tid3 = r["task_id"]
    res3 = wait_task(tid3)
    print(f"  [ASR 服务异常 5xx] status={res3.get('status')}  failure_type={res3.get('failure_type')}  suggest_retry={res3.get('suggest_retry')}")
    print(f"      failure_reason={res3.get('failure_reason')}")
    assert res3["status"] == "failed"
    assert res3["failure_type"] == "asr_service_error"
    assert res3["suggest_retry"] is True

    _, lst = get("/api/v1/tasks?status=failed&page_size=5")
    failed_items = [it for it in lst["items"] if it.get("failure_type")]
    if failed_items:
        it = failed_items[0]
        print(f"  [列表页直接看] failure_type={it.get('failure_type')}  failure_reason={it.get('failure_reason')}  suggest_retry={it.get('suggest_retry')}")
        assert it.get("failure_type") in ("recording_unreachable", "recording_corrupted", "asr_service_error")
        assert isinstance(it.get("failure_reason"), str) and len(it["failure_reason"]) > 5
    print("  ✅ PASS")

    print()
    print("=" * 70)
    print("TEST 2: 真实 HTTP 回调到达 + 真实 4xx/5xx/超时状态码")
    print("=" * 70)

    _callback_received.clear()
    cb_ok = f"http://127.0.0.1:{_callback_server_port}/cb/ok"
    _, r = post("/api/v1/tasks", {
        "recording_url": "https://mock-cdn.example.com/records/normal01.wav",
        "agent_id": "agent_cb_ok",
        "call_type": "outbound",
        "callback_url": cb_ok,
    })
    tid_cb = r["task_id"]
    wait_task(tid_cb)
    time.sleep(4)
    print(f"  [成功回调 200] 本地 mock server 收到 {len(_callback_received)} 条 POST")
    assert len(_callback_received) >= 1, "回调应该到达本地 mock server"
    payload = _callback_received[-1]["payload"]
    print(f"      event={payload.get('event')}  task_id={payload.get('task_id')}  status={payload.get('status')}  total_risks={payload.get('total_risks')}  high={payload.get('high_risk_count')}")
    assert payload.get("event") == "task_finished"
    assert payload.get("task_id") == tid_cb
    assert payload.get("status") == "completed"
    _, cb_hist = get(f"/api/v1/tasks/{tid_cb}/callbacks")
    latest = cb_hist["items"][0]
    print(f"      回调记录: status={latest['status']}  http={latest['http_status_code']}  duration={latest['duration_ms']}ms  triggered_by={latest['triggered_by']}")
    assert latest["status"] == "success"
    assert latest["http_status_code"] == 200
    assert latest["triggered_by"] == "auto"

    _callback_received.clear()
    cb_500 = f"http://127.0.0.1:{_callback_server_port}/cb/500"
    _, r = post("/api/v1/tasks", {
        "recording_url": "https://mock-cdn.example.com/records/normal02.wav",
        "agent_id": "agent_cb_500",
        "call_type": "inbound",
        "callback_url": cb_500,
    })
    tid_cb500 = r["task_id"]
    wait_task(tid_cb500)
    time.sleep(7)
    _, cb_hist = get(f"/api/v1/tasks/{tid_cb500}/callbacks")
    print(f"  [回调失败 HTTP 500] 收到 {cb_hist['total']} 次回调记录  success={cb_hist['success_count']}  failed={cb_hist['failed_count']}")
    for item in cb_hist["items"][:2]:
        print(f"      [{item['id']}] attempt={item['attempt']} status={item['status']} http={item.get('http_status_code')} err={item.get('error_message')}")
    assert cb_hist["total"] >= 3, "500 应该重试 3 次"
    assert cb_hist["failed_count"] >= 3
    assert cb_hist["items"][0]["http_status_code"] == 500

    print("  ✅ PASS")

    print()
    print("=" * 70)
    print("TEST 3: 同一句话触发多类风险（独立 risk_id）+ 分别打不同结论")
    print("=" * 70)

    mixed_text = (
        "您好这里是XX客服中心本次通话将被录音请问有什么可以帮您。"
        "我想咨询下你们的理财。"
        "王先生您好，您加我微信吧微信号是abc123vip，我保证年化收益20%以上零风险保本保息。"
        "您看考虑一下。"
        "好的那我先不打扰您了再见。"
    )
    _, r = post("/api/v1/tasks", {
        "recording_url": "https://mock-cdn.example.com/records/multi_risk.wav",
        "agent_id": "agent_multi_risk",
        "call_type": "outbound",
        "mock_text": mixed_text,
    })
    tid_multi = r["task_id"]
    wait_task(tid_multi)
    _, risks = get(f"/api/v1/tasks/{tid_multi}/risks")
    print(f"  total_risks={risks['total_risks']}  unhandled={risks['unhandled_count']}")
    for rf in risks["risks"]:
        print(f"      risk_id={rf['risk_id']}  seg_idx={rf['segment_index']}  cat={rf['risk_category']}  level={rf['risk_level']}  text='{rf['original_text'][:50]}...'")

    seg_to_risks = {}
    for rf in risks["risks"]:
        seg_to_risks.setdefault(rf["segment_index"], []).append(rf)
    same_seg_multi = [s for s, lst in seg_to_risks.items() if len(lst) >= 2]
    print(f"  同 segment 触发 >=2 类风险的 segment 数量={len(same_seg_multi)}")
    assert len(same_seg_multi) >= 1, "应该有至少一个 segment 同时触发多类风险"

    target_seg = same_seg_multi[0]
    rfs = seg_to_risks[target_seg]
    rf_wechat = next((x for x in rfs if x["risk_category"] == "wechat_solicitation"), None)
    rf_profit = next((x for x in rfs if x["risk_category"] == "profit_guarantee"), None)
    assert rf_wechat is not None
    assert rf_profit is not None
    assert rf_wechat["risk_id"] != rf_profit["risk_id"]

    print(f"  对同 seg={target_seg} 的两条风险分别打结论：")
    print(f"    wechat(risk_id={rf_wechat['risk_id']}) -> confirmed_violation")
    _, upd1 = post(
        f"/api/v1/tasks/{tid_multi}/risks/{rf_wechat['risk_id']}/conclusion",
        {"conclusion": "confirmed_violation", "reviewer": "COMPLIANCE_Zhao", "review_note": "确有加微信引导"},
    )
    print(f"      -> confirmed_count={upd1['confirmed_count']}  unhandled={upd1['unhandled_count']}")
    print(f"    profit(risk_id={rf_profit['risk_id']}) -> false_alarm")
    _, upd2 = post(
        f"/api/v1/tasks/{tid_multi}/risks/{rf_profit['risk_id']}/conclusion",
        {"conclusion": "false_alarm", "reviewer": "COMPLIANCE_Zhao", "review_note": "上下文仅为产品介绍，误报"},
    )
    print(f"      -> false_alarm_count={upd2['false_alarm_count']}  confirmed={upd2['confirmed_count']}  unhandled={upd2['unhandled_count']}")

    assert upd2["confirmed_count"] >= 1
    assert upd2["false_alarm_count"] >= 1
    assert upd2["unhandled_count"] == upd1["unhandled_count"] - 1  # 第二条改的是另一类，unhandled 继续减 1，最终为 0

    _, r_confirmed = get(f"/api/v1/tasks/{tid_multi}/risks?conclusion=confirmed_violation")
    print(f"  筛选 conclusion=confirmed_violation -> total={r_confirmed['total_risks']}")
    assert all(x["conclusion"] == "confirmed_violation" for x in r_confirmed["risks"])
    assert any(x["risk_id"] == rf_wechat["risk_id"] for x in r_confirmed["risks"])
    _, r_false = get(f"/api/v1/tasks/{tid_multi}/risks?conclusion=false_alarm")
    print(f"  筛选 conclusion=false_alarm -> total={r_false['total_risks']}")
    assert any(x["risk_id"] == rf_profit["risk_id"] for x in r_false["risks"])
    print("  ✅ PASS")

    print()
    print("=" * 70)
    print("TEST 4: 手动重试回调 + triggered_by=manual")
    print("=" * 70)

    before = cb_hist["total"]
    _, retry_resp = post(f"/api/v1/tasks/{tid_cb500}/callbacks/retry", {"reviewer": "OPS_Li"})
    time.sleep(3)
    _, cb_hist2 = get(f"/api/v1/tasks/{tid_cb500}/callbacks")
    print(f"  手动重试后回调记录数 {before} -> {cb_hist2['total']}")
    latest = cb_hist2["items"][0]
    print(f"    latest triggered_by={latest['triggered_by']}  http={latest.get('http_status_code')}  err={latest.get('error_message')}")
    assert cb_hist2["total"] == before + 1
    assert latest["triggered_by"] == "manual"
    print("  ✅ PASS")

    print()
    print("=" * 70)
    print("TEST 5: 主管统计接口（按坐席/通话类型/日期范围）")
    print("=" * 70)

    d = today()
    _, stats = get(f"/api/v1/stats/supervisor?date_from={d}&date_to={d}&group_by=agent")
    print(f"  [group_by=agent] 全量 total_tasks={stats['total_tasks']}  failed={stats['total_failed']}  risks={stats['total_risks']}  confirmed={stats['total_confirmed']}")
    print(f"      分组数={len(stats['items'])}")
    for item in stats["items"][:5]:
        print(
            f"      agent={item['agent_id']} date={item['date']} total={item['total_tasks']} "
            f"failed={item['failed_tasks']}({item['failed_rate']:.0%}) "
            f"risky={item['tasks_with_risk']}({item['risk_rate']:.0%}) "
            f"risks={item['total_risks']} confirmed={item['confirmed_violations']}"
        )
    assert stats["total_tasks"] >= 1
    agent_multi_item = next((i for i in stats["items"] if i["agent_id"] == "agent_multi_risk"), None)
    assert agent_multi_item is not None
    assert agent_multi_item["confirmed_violations"] >= 1
    assert agent_multi_item["total_risks"] >= 2

    _, stats2 = get(f"/api/v1/stats/supervisor?date_from={d}&date_to={d}&group_by=agent_call_type")
    print(f"  [group_by=agent_call_type] 分组数={len(stats2['items'])}")
    call_types = set(i["call_type"] for i in stats2["items"] if i["call_type"])
    print(f"      覆盖 call_type={sorted(call_types)}")
    assert len(call_types) >= 2

    _, stats3 = get(f"/api/v1/stats/supervisor?date_from={d}&date_to={d}&agent_id=agent_multi_risk")
    print(f"  [单坐席筛选 agent_multi_risk] total_tasks={stats3['total_tasks']} confirmed={stats3['total_confirmed']}")
    assert stats3["total_tasks"] >= 1
    assert stats3["total_confirmed"] >= 1
    print("  ✅ PASS")

    print()
    print("=" * 70)
    print("ALL TESTS PASSED 🎉")
    print("=" * 70)


if __name__ == "__main__":
    main()
