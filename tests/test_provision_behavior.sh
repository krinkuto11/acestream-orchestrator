#!/bin/bash
# Test script to verify orchestrator provisioning behavior with acexy proxy
# This script can be run on the actual server to test the integration

set -e

echo "🧪 Testing Orchestrator Provisioning Behavior with Acexy Integration"
echo "====================================================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
ORCHESTRATOR_URL="${ORCHESTRATOR_URL:-http://localhost:8000}"
API_KEY="${API_KEY:-holaholahola}"

echo "Configuration:"
echo "  Orchestrator URL: $ORCHESTRATOR_URL"
echo "  API Key: ${API_KEY:0:5}..."
echo ""

# Function to make API call
call_api() {
    local method=$1
    local endpoint=$2
    local data=$3
    
    if [ "$method" = "GET" ]; then
        curl -s -X GET "$ORCHESTRATOR_URL$endpoint" -H "Authorization: Bearer $API_KEY"
    elif [ "$method" = "POST" ]; then
        curl -s -X POST "$ORCHESTRATOR_URL$endpoint" -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" -d "$data"
    fi
}

# Test 1: Check orchestrator is accessible
echo "📋 Test 1: Checking orchestrator accessibility..."
if response=$(call_api GET "/engines" 2>&1); then
    echo -e "${GREEN}✅ Orchestrator is accessible${NC}"
    initial_count=$(echo "$response" | jq '. | length' 2>/dev/null || echo "0")
    echo "   Current engine count: $initial_count"
else
    echo -e "${RED}❌ Cannot reach orchestrator at $ORCHESTRATOR_URL${NC}"
    exit 1
fi
echo ""

# Test 2: Check orchestrator status endpoint
echo "📋 Test 2: Checking orchestrator status endpoint..."
if status=$(call_api GET "/orchestrator/status" 2>&1); then
    echo -e "${GREEN}✅ Status endpoint accessible${NC}"
    echo "   Status: $(echo "$status" | jq -r '.status' 2>/dev/null || echo "unknown")"
    echo "   Engines: $(echo "$status" | jq -r '.engines.total' 2>/dev/null || echo "?") total, $(echo "$status" | jq -r '.engines.running' 2>/dev/null || echo "?") running"
    echo "   VPN Enabled: $(echo "$status" | jq -r '.vpn.enabled' 2>/dev/null || echo "?")"
    echo "   VPN Connected: $(echo "$status" | jq -r '.vpn.connected' 2>/dev/null || echo "?")"
    echo "   Can Provision: $(echo "$status" | jq -r '.provisioning.can_provision' 2>/dev/null || echo "?")"
else
    echo -e "${YELLOW}⚠️  Status endpoint not available (may need to update orchestrator)${NC}"
fi
echo ""

# Test 3: Check VPN status
echo "📋 Test 3: Checking VPN status..."
if vpn_status=$(call_api GET "/vpn/status" 2>&1); then
    echo -e "${GREEN}✅ VPN status endpoint accessible${NC}"
    vpn_enabled=$(echo "$vpn_status" | jq -r '.enabled' 2>/dev/null || echo "false")
    vpn_connected=$(echo "$vpn_status" | jq -r '.connected' 2>/dev/null || echo "false")
    vpn_health=$(echo "$vpn_status" | jq -r '.health' 2>/dev/null || echo "unknown")
    
    echo "   Enabled: $vpn_enabled"
    echo "   Connected: $vpn_connected"
    echo "   Health: $vpn_health"
    
    if [ "$vpn_enabled" = "true" ] && [ "$vpn_connected" != "true" ]; then
        echo -e "${YELLOW}⚠️  VPN is enabled but not connected - provisioning may fail${NC}"
    fi
else
    echo -e "${RED}❌ VPN status check failed${NC}"
fi
echo ""

# Test 4: Provision a new engine
echo "📋 Test 4: Provisioning a new engine..."
provision_data='{"labels": {"test": "acexy-integration"}, "env": {}}'
if provision_response=$(call_api POST "/provision/acestream" "$provision_data" 2>&1); then
    container_id=$(echo "$provision_response" | jq -r '.container_id' 2>/dev/null || echo "")
    container_name=$(echo "$provision_response" | jq -r '.container_name' 2>/dev/null || echo "")
    host_port=$(echo "$provision_response" | jq -r '.host_http_port' 2>/dev/null || echo "")
    
    if [ -n "$container_id" ] && [ "$container_id" != "null" ]; then
        echo -e "${GREEN}✅ Provisioning successful${NC}"
        echo "   Container ID: ${container_id:0:12}"
        echo "   Container Name: $container_name"
        echo "   Host HTTP Port: $host_port"
        
        # Test 5: Verify engine appears in /engines immediately
        echo ""
        echo "📋 Test 5: Verifying engine appears in state immediately..."
        sleep 2  # Small delay to ensure state sync
        
        if engines=$(call_api GET "/engines" 2>&1); then
            new_count=$(echo "$engines" | jq '. | length' 2>/dev/null || echo "0")
            
            if [ "$new_count" -gt "$initial_count" ]; then
                echo -e "${GREEN}✅ CRITICAL: Engine appeared in state (count: $initial_count → $new_count)${NC}"
                
                # Check if our specific engine is in the list
                if echo "$engines" | jq -e ".[] | select(.container_id == \"$container_id\")" > /dev/null 2>&1; then
                    echo -e "${GREEN}✅ Provisioned engine found in /engines list${NC}"
                else
                    echo -e "${YELLOW}⚠️  Engine count increased but specific engine not found${NC}"
                fi
            else
                echo -e "${RED}❌ CRITICAL: Engine NOT in state after provisioning!${NC}"
                echo "   This would cause acexy proxy to fail"
                echo "   Expected count > $initial_count, got $new_count"
            fi
        else
            echo -e "${RED}❌ Failed to get engines list${NC}"
        fi
        
        # Test 6: Check Docker container is running
        echo ""
        echo "📋 Test 6: Verifying Docker container is running..."
        if docker ps --filter "id=${container_id:0:12}" --format "{{.Status}}" 2>/dev/null | grep -q "Up"; then
            echo -e "${GREEN}✅ Docker container is running${NC}"
        else
            echo -e "${RED}❌ Docker container is NOT running${NC}"
            echo "   This indicates provisioning created container but it failed to start"
        fi
        
        # Test 7: Wait and check if engine becomes healthy
        echo ""
        echo "📋 Test 7: Checking engine health (waiting 15s for engine to initialize)..."
        sleep 15
        
        if health_status=$(call_api GET "/health/status" 2>&1); then
            healthy_count=$(echo "$health_status" | jq -r '.healthy_engines' 2>/dev/null || echo "0")
            echo "   Healthy engines: $healthy_count"
            
            if [ "$healthy_count" -gt "0" ]; then
                echo -e "${GREEN}✅ At least one engine is healthy${NC}"
            else
                echo -e "${YELLOW}⚠️  No healthy engines yet (may need more time to initialize)${NC}"
            fi
        fi
        
        # Cleanup: Remove the test container
        echo ""
        echo "📋 Cleanup: Removing test container..."
        if call_api DELETE "/containers/$container_id" > /dev/null 2>&1; then
            echo -e "${GREEN}✅ Test container removed${NC}"
        else
            echo -e "${YELLOW}⚠️  Could not remove test container via API${NC}"
            echo "   You may need to manually remove: docker rm -f ${container_id:0:12}"
        fi
        
    else
        echo -e "${RED}❌ Provisioning failed - no container ID in response${NC}"
        echo "   Response: $provision_response"
    fi
else
    echo -e "${RED}❌ Provisioning request failed${NC}"
    echo "   Error: $provision_response"
fi

echo ""
echo "====================================================================="
echo "🏁 Test Complete"
echo ""
echo "Summary:"
echo "  - If all tests passed, orchestrator is ready for acexy integration"
echo "  - If Test 5 failed, update orchestrator to include reindex after provision"
echo "  - If VPN tests show issues, check Gluetun configuration"
echo ""
