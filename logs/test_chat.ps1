$body = '{"message": "我今天完成了一个重要的项目,感觉很有成就感"}'
$resp = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8765/chat' -ContentType 'application/json' -Body $body -TimeoutSec 60
$resp | ConvertTo-Json -Depth 4
