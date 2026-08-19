$body = '{"message": "我今天完成了一个重要的项目,感觉很有成就感"}'
$req = [System.Net.HttpWebRequest]::Create('http://127.0.0.1:8765/chat')
$req.Method = 'POST'
$req.ContentType = 'application/json; charset=utf-8'
$req.ContentLength = $body.Length
$req.Timeout = 60000
$bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
$stream = $req.GetRequestStream()
$stream.Write($bytes, 0, $bytes.Length)
$stream.Close()
try {
    $resp = $req.GetResponse()
    $reader = New-Object System.IO.StreamReader($resp.GetResponseStream(), [System.Text.Encoding]::UTF8)
    $text = $reader.ReadToEnd()
    Write-Host $text
} catch {
    $ex = $_.Exception
    if ($ex.Response) {
        $reader = New-Object System.IO.StreamReader($ex.Response.GetResponseStream(), [System.Text.Encoding]::UTF8)
        $text = $reader.ReadToEnd()
        Write-Host "ERROR_BODY: $text"
    } else {
        Write-Host "ERROR: $ex"
    }
}
