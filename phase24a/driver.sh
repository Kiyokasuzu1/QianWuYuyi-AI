#!/bin/bash
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
LOCK=/root/QianWuYuyi-AI/phase24a/driver.lock
DONE=/root/QianWuYuyi-AI/phase24a/driver.done
LOG=/root/QianWuYuyi-AI/phase24a/driver_output.log
if [ -f "$DONE" ]; then exit 0; fi
if [ -f "$LOCK" ]; then exit 0; fi
touch "$LOCK"
{
echo "=== phase24a driver start $(date '+%F %T %z') ==="
echo "-- pre memory.json --"
ls -la /root/QianWuYuyi-AI/data/memory.json
sha256sum /root/QianWuYuyi-AI/data/memory.json
echo "-- stop yuyi-api --"
systemctl stop yuyi-api.service
sleep 3
echo "is-active: $(systemctl is-active yuyi-api.service)"
echo "-- import --"
/root/QianWuYuyi-AI/venv/bin/python /root/QianWuYuyi-AI/phase24a_import.py --payload /root/QianWuYuyi-AI/phase24a/t1_payload.jsonl --memory-path /root/QianWuYuyi-AI/data/memory.json --report /root/QianWuYuyi-AI/phase24a/import_report.json
echo "import exit: $?"
echo "-- verify --"
/root/QianWuYuyi-AI/venv/bin/python /root/QianWuYuyi-AI/phase24a_verify.py --memory-path /root/QianWuYuyi-AI/data/memory.json --pre /root/QianWuYuyi-AI/phase24a/memory.pre_import.json --imported-ids /root/QianWuYuyi-AI/phase24a/imported_ids.txt --out /root/QianWuYuyi-AI/phase24a/verify_report.json
echo "verify exit: $?"
echo "-- post memory.json --"
sha256sum /root/QianWuYuyi-AI/data/memory.json
echo "-- start yuyi-api --"
systemctl start yuyi-api.service
sleep 5
echo "is-active: $(systemctl is-active yuyi-api.service)"
echo "=== phase24a driver done $(date '+%F %T %z') ==="
} > "$LOG" 2>&1
touch "$DONE"
