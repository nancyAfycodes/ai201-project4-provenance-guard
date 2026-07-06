#!/bin/bash
# test_rate_limit.sh
#
# Milestone 5 checkpoint test: sends 12 rapid requests to /submit,
# more than the configured 10/minute limit. Expect the first 10 to
# return 201 (Created) and the remaining 2 to return 429 (Too Many
# Requests).
#
# Run this while `python app.py` is running in another terminal.

for i in $(seq 1 12); do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:5000/submit \
    -H "Content-Type: application/json" \
    -d '{"text": "This is a test submission for rate limit testing purposes only.", "creator_id": "ratelimit-test"}'
done
