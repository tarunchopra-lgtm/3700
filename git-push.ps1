$remark = Read-Host "Enter remarks"
git add .
git commit -m "$remark"
git push
Write-Host "Done!" -ForegroundColor Green