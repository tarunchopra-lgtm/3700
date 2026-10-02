$ErrorActionPreference = "Stop"
$remote = "origin"
$branch = "main"
$ignoredPaths = @("lists/fomo_trade.txt", "live/lists/fomo_trade.txt")

git fetch $remote
if ($LASTEXITCODE -ne 0) {
	throw "Could not fetch $remote. Push was not attempted."
}

# The remote repository is authoritative for these runtime configuration files.
git restore --worktree -- $ignoredPaths
if ($LASTEXITCODE -ne 0) {
	throw "Could not restore the remote-managed configuration files. Push was not attempted."
}

git add .
git restore --staged -- $ignoredPaths

git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
	$remark = Read-Host "Enter remarks"
	git commit -m "$remark"
	if ($LASTEXITCODE -ne 0) {
		throw "Commit failed. Push was not attempted."
	}
} else {
	Write-Host "No staged changes to commit." -ForegroundColor Yellow
}

$divergence = (git rev-list --left-right --count "HEAD...$remote/$branch").Trim().Split()
$ahead = [int]$divergence[0]
$behind = [int]$divergence[1]
if ($behind -gt 0) {
	git pull --rebase $remote $branch
	if ($LASTEXITCODE -ne 0) {
		throw "Automatic rebase failed. Resolve the conflict, then run this script again."
	}
}

git push $remote $branch
if ($LASTEXITCODE -ne 0) {
	throw "Push failed. The remote branch was not changed."
}

Write-Host "Done!" -ForegroundColor Green