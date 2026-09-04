from pathlib import Path
import re
p=Path('index.html')
s=p.read_text(encoding='utf-8')
s,n=re.subn(r'loadBranches=async function\(\)\{await originalLoadBranches\(\);','loadBranches=async function(){const hadBranch=currentBranchId;await originalLoadBranches();',s,count=1)
s=s.replace("const current=branches.find(b=>Number(b.id)===Number(currentBranchId));if(mc&&!current)currentBranchId=mc.id;","if(mc&&!hadBranch)currentBranchId=mc.id;")
p.write_text(s,encoding='utf-8')
assert 'const hadBranch=currentBranchId' in s
assert 'if(mc&&!hadBranch)currentBranchId=mc.id;' in s
print('branch default fixed')
