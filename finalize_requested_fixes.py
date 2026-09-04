from pathlib import Path
p=Path('index.html')
s=p.read_text(encoding='utf-8')
s=s.replace("const originalLoadBranches=loadBranches; loadBranches=async function(){await originalLoadBranches();const mc=", "const originalLoadBranches=loadBranches; loadBranches=async function(){const hadBranch=currentBranchId;await originalLoadBranches();const mc=")
s=s.replace("const current=branches.find(b=>Number(b.id)===Number(currentBranchId));if(mc&&!current)currentBranchId=mc.id;", "if(mc&&!hadBranch)currentBranchId=mc.id;")
p.write_text(s,encoding='utf-8')
print('final branch-default fix applied')
