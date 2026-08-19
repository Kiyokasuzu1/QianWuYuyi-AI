$body = [System.Text.Encoding]::UTF8.GetBytes('{"message": "我今天完成了一个重要的项目,感觉很有成就感"}')
$req = [System.Net.HttpWebRequest]::Create('http://127.0.0.1:8765/chat')
$req.Method = 'POST'
$req.ContentType = 'application/json; charset=utf-8'
$req.ContentLength = $body.Length
$req.Timeout = 60000
$stream = $req.GetRequestStream()
$stream.Write($body, 0, $body.Length)
$stream.Close()
$resp = $req.GetResponse()
$reader = New-Object System.IO.StreamReader($resp.GetResponseStream(), [System.Text.Encoding]::UTF8)
$text = $reader.ReadToEnd()
$text
