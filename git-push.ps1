$remark = Read-Host "Enter remarks"
git add .
git commit -m "$remark"
git push --force-with-lease origin main
Write-Host "Done!" -ForegroundColor Green