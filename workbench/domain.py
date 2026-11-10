import re
def in_range(serial,prefix,lo,hi):
    match=re.fullmatch(re.escape(prefix)+r'(\d+)',serial)
    return bool(match) and lo<=int(match[1])<=hi
def history(events,cutoff):
    return sorted((x for x in events if x['date']<=cutoff),key=lambda x:(x['date'],x['id']))
def project(rows,fields):return [{k:r[k] for k in fields} for r in rows]
TOOLS={'in_range':in_range,'history':history,'project':project}
