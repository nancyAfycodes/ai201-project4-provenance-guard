# test_rate_limit.ps1
#
# PowerShell equivalent of test_rate_limit.sh, for Windows users without
# bash/WSL. Sends 12 rapid requests to /submit (more than the configured
# 10/minute limit) and prints the status code of each.
#
# Run this while `python app.py` is running in another terminal.
#
# Expected output: 10x 201 (Created), then 2x 429 (Too Many Requests).

1..12 | ForEach-Object {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:5000/submit" `
            -Method Post `
            -Body '{"text": "This is a test submission for rate limit testing purposes only.", "creator_id": "ratelimit-test"}' `
            -ContentType "application/json" `
            -SkipHttpErrorCheck
        Write-Output $response.StatusCode
    } catch {
        # Older PowerShell versions (5.1) throw on non-2xx instead of
        # supporting -SkipHttpErrorCheck. Fall back to reading the
        # status code off the exception's response.
        Write-Output $_.Exception.Response.StatusCode.value__
    }
}
