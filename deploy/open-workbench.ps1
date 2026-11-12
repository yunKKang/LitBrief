# Run in a local terminal and keep it open while reviewing.
param(
    [string]$Server = 'ubuntu@43.134.100.87',
    [string]$Identity = "$env:USERPROFILE\.ssh\server_43_134_100_87"
)
Write-Host 'After SSH connects, open http://127.0.0.1:8766/ . Ctrl+C closes the tunnel.'
ssh -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes -i $Identity -N -L 127.0.0.1:8766:127.0.0.1:8766 $Server
