$ErrorActionPreference = "Stop"
$remote = "origin"
$branch = "main"

git fetch $remote
if ($LASTEXITCODE -ne 0) {
	throw "Could not fetch $remote. Push was not attempted."
}

$divergence = (git rev-list --left-right --count "HEAD...$remote/$branch").Trim().Split()
$ahead = [int]$divergence[0]
$behind = [int]$divergence[1]
if ($behind -gt 0) {
	throw "Local branch is $behind commit(s) behind $remote/$branch. Run 'git pull --rebase $remote $branch', resolve conflicts, then run this script again."
}

$remark = Read-Host "Enter remarks"
git add .
git restore --staged -- lists/fomo_trade.txt live/lists/fomo_trade.txt

git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
	git commit -m "$remark"
	if ($LASTEXITCODE -ne 0) {
		throw "Commit failed. Push was not attempted."
	}
} else {
	Write-Host "No staged changes to commit." -ForegroundColor Yellow
}

git push $remote $branch
if ($LASTEXITCODE -ne 0) {
	throw "Push failed. The remote branch was not changed."
}

Write-Host "Done!" -ForegroundColor Green