$remark = Read-Host "Enter remarks"
git add .
git restore --staged -- lists/fomo_trade.txt live/lists/fomo_trade.txt
git commit -m "$remark"
git push --force-with-lease origin main
Write-Host "Done!" -ForegroundColor Green