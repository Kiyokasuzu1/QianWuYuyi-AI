#!/bin/bash
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
LOCK=/root/QianWuYuyi-AI/phase24c/driver.lock
DONE=/root/QianWuYuyi-AI/phase24c/driver.done
LOG=/root/QianWuYuyi-AI/phase24c/driver_output.log
if [ -f "$DONE" ]; then exit 0; fi
if [ -f "$LOCK" ]; then exit 0; fi
touch "$LOCK"
{
echo "=== phase24c driver start $(date '+%F %T %z') ==="
sha256sum /root/QianWuYuyi-AI/data/memory.json
systemctl stop yuyi-api.service; sleep 3
echo "is-active: $(systemctl is-active yuyi-api.service)"
/root/QianWuYuyi-AI/venv/bin/python /root/QianWuYuyi-AI/phase24c_import.py --payload /root/QianWuYuyi-AI/phase24c/t2a_payload.jsonl --memory-path /root/QianWuYuyi-AI/data/memory.json --report /root/QianWuYuyi-AI/phase24c/import_report.json
echo "import exit: $?"
/root/QianWuYuyi-AI/venv/bin/python /root/QianWuYuyi-AI/phase24a_verify.py --memory-path /root/QianWuYuyi-AI/data/memory.json --pre /root/QianWuYuyi-AI/phase24c/memory.pre_t2a_import.json --imported-ids /root/QianWuYuyi-AI/phase24c/imported_ids.txt --out /root/QianWuYuyi-AI/phase24c/verify_report.json
echo "verify exit: $?"
sha256sum /root/QianWuYuyi-AI/data/memory.json
systemctl start yuyi-api.service; sleep 5
echo "is-active: $(systemctl is-active yuyi-api.service)"
echo "=== phase24c driver done $(date '+%F %T %z') ==="
} > "$LOG" 2>&1
touch "$DONE"
