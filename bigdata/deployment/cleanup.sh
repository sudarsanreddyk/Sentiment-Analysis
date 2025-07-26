# Create the script in the deployment folder
cat > cleanup.sh << 'EOF'
#!/bin/bash

echo " Cleaning up Reddit Sentiment Analysis deployment..."

NAMESPACE="reddit-sentiment"

echo " Working with namespace: $NAMESPACE"
echo "Current directory: $(pwd)"
echo ""

# Function to check and delete resources
delete_resources() {
    local resource_type=$1
    echo "  Deleting $resource_type..."
    
    local resources=$(kubectl get $resource_type -n $NAMESPACE --no-headers 2>/dev/null | awk '{print $1}')
    
    if [ -n "$resources" ]; then
        for resource in $resources; do
            echo "   - Deleting $resource_type: $resource"
            kubectl delete $resource_type $resource -n $NAMESPACE --grace-period=0 --force 2>/dev/null
        done
        echo "    All $resource_type deleted"
    else
        echo "   ℹ  No $resource_type found"
    fi
    echo ""
}

# Check if namespace exists
if ! kubectl get namespace $NAMESPACE >/dev/null 2>&1; then
    echo " Namespace $NAMESPACE does not exist. Nothing to cleanup."
    exit 0
fi

echo " Starting cleanup process..."
echo ""

# Delete resources in order
delete_resources "deployments"
delete_resources "jobs" 
delete_resources "services"
delete_resources "configmaps"
delete_resources "pvc"
delete_resources "pods"

# Wait for termination
echo " Waiting 10 seconds for resources to terminate..."
sleep 10

# Check remaining resources
echo " Checking remaining resources..."
remaining=$(kubectl get all -n $NAMESPACE --no-headers 2>/dev/null | wc -l)
if [ $remaining -eq 0 ]; then
    echo " All application resources cleaned up successfully"
else
    echo "  Some resources may still be terminating:"
    kubectl get all -n $NAMESPACE 2>/dev/null
fi

echo ""
echo "  Namespace cleanup options:"

# Optional: Delete namespace
read -p "Do you want to delete the entire namespace '$NAMESPACE'? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "  Deleting namespace $NAMESPACE..."
    kubectl delete namespace $NAMESPACE --grace-period=0 --force
    echo " Namespace $NAMESPACE deleted"
else
    echo "ℹ  Namespace $NAMESPACE preserved"
fi

echo ""
echo "  Cluster cleanup options:"

# Optional: Delete cluster
read -p "Do you want to delete the entire GKE cluster 'reddit-sentiment-cluster'? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "  Deleting GKE cluster..."
    echo "  This will take 5-10 minutes and stop all billing"
    gcloud container clusters delete reddit-sentiment-cluster --zone=us-central1-a --quiet
    echo " GKE cluster deleted"
else
    echo "ℹ  GKE cluster preserved"
fi

echo ""
echo "=================================================="
echo " Cleanup completed!"
echo ""
echo "Summary:"
echo "- All pods, deployments, services stopped"
echo "- All configmaps and PVCs removed"
echo "- Load balancers and external IPs released"
echo ""
echo " This should stop billing for application resources."
echo " You can redeploy anytime using your YAML files in this folder."
echo "=================================================="
EOF