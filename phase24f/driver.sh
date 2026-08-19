#!/bin/bash
# Phase 2.4-F T2c A类 受控导入 driver(一次性,由宝塔 cron 每分钟触发,锁+DONE 双保险)
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ROOT=/root/QianWuYuyi-AI
LOCK=$ROOT/phase24f/driver.lock
DONE=$ROOT/phase24f/driver.done
LOG=$ROOT/phase24f/driver_output.log
if [ -f "$DONE" ]; then exit 0; fi
if [ -f "$LOCK" ]; then exit 0; fi
touch "$LOCK"
{
echo "=== phase24f driver start $(date '+%F %T %z') ==="
echo "is-active before: $(systemctl is-active yuyi-api.service)"
echo "--- memory before ---"
sha256sum $ROOT/data/memory.json

mkdir -p $ROOT/phase24f/backup/state_snapshot
cp $ROOT/data/memory.json $ROOT/phase24f/backup/memory.pre_t2c_import.json
sha256sum $ROOT/phase24f/backup/memory.pre_t2c_import.json > $ROOT/phase24f/backup/memory.pre_t2c_import.json.sha256
for f in emotion_state emotional_traces growth_state personality_growth_history relationship_state runtime_state self_model agent_server_status; do
  cp $ROOT/data/$f.json $ROOT/phase24f/backup/state_snapshot/$f.json
  sha256sum $ROOT/phase24f/backup/state_snapshot/$f.json >> $ROOT/phase24f/backup/state_snapshot/pre_hashes.txt
done
echo "--- backup done $(date '+%F %T %z') ---"

systemctl stop yuyi-api.service; sleep 3
echo "is-active stopped: $(systemctl is-active yuyi-api.service)"

$ROOT/venv/bin/python $ROOT/phase24f_import.py --payload $ROOT/phase24f/t2c_payload.jsonl --memory-path $ROOT/data/memory.json --report $ROOT/phase24f/import_report.json
echo "import exit: $?"

$ROOT/venv/bin/python $ROOT/phase24f_verify.py --memory-path $ROOT/data/memory.json --pre $ROOT/phase24f/backup/memory.pre_t2c_import.json --imported-ids $ROOT/phase24f/t2c_ids.txt --out $ROOT/phase24f/verify_report.json
echo "verify exit: $?"

echo "--- memory after ---"
sha256sum $ROOT/data/memory.json

systemctl start yuyi-api.service; sleep 6
echo "is-active after: $(systemctl is-active yuyi-api.service)"
for f in emotion_state emotional_traces growth_state personality_growth_history relationship_state runtime_state self_model agent_server_status; do
  sha256sum $ROOT/data/$f.json >> $ROOT/phase24f/backup/state_snapshot/post_hashes.txt
done
echo "=== phase24f driver done $(date '+%F %T %z') ==="
} > "$LOG" 2>&1
touch "$DONE"
