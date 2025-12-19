#!/bin/bash

# Start server in background
./run.sh 8081 &
SERVER_PID=$!

# Wait for startup
echo "Waiting for server startup..."
sleep 3

# Test endpoints
echo "Testing /papers endpoint..."
curl -s http://localhost:8081/papers | python -m json.tool > /dev/null
if [ $? -eq 0 ]; then
    echo "[PASS] /papers endpoint working"
else
    echo "[FAIL] /papers endpoint failed"
fi

echo "Testing /stats endpoint..."
curl -s http://localhost:8081/stats | python -m json.tool > /dev/null
if [ $? -eq 0 ]; then
    echo "[PASS] /stats endpoint working"
else
    echo "[FAIL] /stats endpoint failed"
fi

echo "Testing search endpoint..."
curl -s "http://localhost:8081/search?q=machine" | python -m json.tool > /dev/null
if [ $? -eq 0 ]; then
    echo "[PASS] /search endpoint working"
else
    echo "[FAIL] /search endpoint failed"
fi

echo "Testing 404 handling..."
RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8081/invalid)
if [ "$RESPONSE" = "404" ]; then
    echo "[PASS] 404 handling working"
else
    echo "[FAIL] 404 handling failed (got $RESPONSE)"
fi

# Cleanup
kill $SERVER_PID 2>/dev/null
echo "Tests complete"