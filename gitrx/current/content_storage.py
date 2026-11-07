"""Exact text CAS with stable native instances and legacy read/write views."""
import hashlib
import re
import sqlite3

VERSION='exact-text-cas-1'
FIELDS={
 'sections':('section_instances',('title','source_xml','narrative_xml','own_text')),
 'narrative_blocks':('block_instances',('text','normalized_text')),
 'narrative_tables':('table_instances',('source_xml','structured_json','flattened_text')),
 'section_excerpts':('excerpt_instances',('source_xml','extracted_text')),
}

def text_sha(value):
    if value is None:return None
    if not isinstance(value,str):raise TypeError('Content must be exact text')
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class InsertCursor:
    def __init__(self,cursor,ident):self.cursor=cursor;self.lastrowid=ident
    def __getattr__(self,name):return getattr(self.cursor,name)
    def __iter__(self):return iter(self.cursor)


class ContentConnection(sqlite3.Connection):
    content_layout=False
    def enable_content_layout(self):
        self.create_function('content_sha',1,text_sha,deterministic=True)
        self.content_layout=True
    def execute(self,sql,parameters=()):
        cursor=super().execute(sql,parameters)
        if self.content_layout:
            found=re.match(r'^\s*INSERT(?:\s+OR\s+\w+)?\s+INTO\s+["`]?([\w]+)',sql,re.I)
            if found and found[1] in FIELDS:
                # SQLite restores last_insert_rowid after an INSTEAD OF trigger.
                # The parser uses one writer; the inserted native row is MAX(id).
                physical=FIELDS[found[1]][0]
                ident=super().execute('SELECT MAX(id) FROM '+physical).fetchone()[0]
                return InsertCursor(cursor,ident)
        return cursor


def migrate(c):
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='content_store_meta'").fetchone():
        return {'already_migrated':True}
    c.commit();c.execute('PRAGMA foreign_keys=OFF');c.enable_content_layout()
    inventory={};before={}
    try:
        c.execute('BEGIN IMMEDIATE')
        c.execute('CREATE TABLE content_blobs(sha256 TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID')
        c.execute('''CREATE TRIGGER content_collision BEFORE INSERT ON content_blobs
          WHEN EXISTS(SELECT 1 FROM content_blobs WHERE sha256=NEW.sha256 AND value IS NOT NEW.value)
          BEGIN SELECT RAISE(ABORT,'content hash collision'); END''')
        c.execute('''CREATE TRIGGER content_immutable BEFORE UPDATE ON content_blobs
          BEGIN SELECT RAISE(ABORT,'content objects are immutable'); END''')
        c.execute('DROP TRIGGER IF EXISTS blocks_insert');c.execute('DROP TRIGGER IF EXISTS blocks_delete')
        for old,(physical,fields) in FIELDS.items():
            columns=[r[1] for r in c.execute('PRAGMA table_info('+old+')')]
            inventory[old]=columns
            before[old]=c.execute('SELECT COUNT(*) FROM '+old).fetchone()[0]
            c.execute('ALTER TABLE '+old+' RENAME TO '+physical)
            for field in fields:
                c.execute(f'ALTER TABLE {physical} RENAME COLUMN {field} TO {field}_sha256')
            select=c.execute('SELECT id,'+','.join(f+'_sha256' for f in fields)+' FROM '+physical)
            while True:
                rows=select.fetchmany(32)
                if not rows:break
                for row in rows:
                    refs=[]
                    for value in list(row)[1:]:
                        digest=text_sha(value);refs.append(digest)
                        if value is not None:c.execute('INSERT OR IGNORE INTO content_blobs VALUES(?,?)',(digest,value))
                    c.execute('UPDATE '+physical+' SET '+','.join(f+'_sha256=?' for f in fields)+' WHERE id=?',(*refs,row[0]))
            expressions=[f'(SELECT value FROM content_blobs WHERE sha256=i.{col}_sha256) AS {col}' if col in fields else 'i.'+col for col in columns]
            c.execute('CREATE VIEW '+old+' AS SELECT '+','.join(expressions)+' FROM '+physical+' i')
            store=' '.join(f'INSERT OR IGNORE INTO content_blobs SELECT content_sha(NEW.{f}),NEW.{f} WHERE NEW.{f} IS NOT NULL;' for f in fields)
            mapped=[col+'_sha256' if col in fields else col for col in columns]
            vals=[f'content_sha(NEW.{col})' if col in fields else 'NEW.'+col for col in columns]
            c.execute('CREATE TRIGGER cas_'+old+'_insert INSTEAD OF INSERT ON '+old+' BEGIN '+store+
                ' INSERT INTO '+physical+'('+','.join(mapped)+') VALUES('+','.join(vals)+'); END')
            c.execute('CREATE TRIGGER cas_'+old+'_update INSTEAD OF UPDATE ON '+old+' BEGIN '+store+
                ' UPDATE '+physical+' SET '+','.join(k+'='+v for k,v in zip(mapped,vals))+' WHERE id=OLD.id; END')
            c.execute('CREATE TRIGGER cas_'+old+'_delete INSTEAD OF DELETE ON '+old+
                ' BEGIN DELETE FROM '+physical+' WHERE id=OLD.id; END')
        c.execute('''CREATE TRIGGER blocks_insert AFTER INSERT ON block_instances BEGIN
          INSERT INTO blocks_fts(rowid,text) SELECT NEW.id,value FROM content_blobs WHERE sha256=NEW.normalized_text_sha256; END''')
        c.execute('''CREATE TRIGGER blocks_delete AFTER DELETE ON block_instances BEGIN
          INSERT INTO blocks_fts(blocks_fts,rowid,text) SELECT 'delete',OLD.id,value FROM content_blobs WHERE sha256=OLD.normalized_text_sha256; END''')
        c.execute('''CREATE TRIGGER blocks_update BEFORE UPDATE ON block_instances BEGIN
          INSERT INTO blocks_fts(blocks_fts,rowid,text) SELECT 'delete',OLD.id,value FROM content_blobs WHERE sha256=OLD.normalized_text_sha256; END''')
        c.execute('''CREATE TRIGGER blocks_updated AFTER UPDATE ON block_instances BEGIN
          INSERT INTO blocks_fts(rowid,text) SELECT NEW.id,value FROM content_blobs WHERE sha256=NEW.normalized_text_sha256; END''')
        c.execute('CREATE TABLE content_store_meta(version TEXT PRIMARY KEY,compatibility_columns TEXT NOT NULL)')
        import json
        c.execute('INSERT INTO content_store_meta VALUES(?,?)',(VERSION,json.dumps(inventory,sort_keys=True)))
        violations=c.execute('PRAGMA foreign_key_check').fetchall()
        if violations:raise ValueError('Migration foreign-key violation')
        c.commit()
    except BaseException:
        c.rollback();raise
    finally:c.execute('PRAGMA foreign_keys=ON')
    after={old:c.execute('SELECT COUNT(*) FROM '+old).fetchone()[0] for old in FIELDS}
    if before!=after:raise ValueError('Instance count changed')
    return {'version':VERSION,'instance_counts':after,'unique_text_objects':c.execute('SELECT COUNT(*) FROM content_blobs').fetchone()[0]}


def verify(c):
    bad=sum(text_sha(r['value'])!=r['sha256'] for r in c.execute('SELECT * FROM content_blobs'))
    dangling={}
    for old,(physical,fields) in FIELDS.items():
        for field in fields:
            dangling[old+'.'+field]=c.execute(f'''SELECT COUNT(*) FROM {physical} i LEFT JOIN content_blobs b
              ON b.sha256=i.{field}_sha256 WHERE i.{field}_sha256 IS NOT NULL AND b.sha256 IS NULL''').fetchone()[0]
    return {'content_hash_failures':bad,'dangling_content_references':sum(dangling.values()),
            'foreign_key_violations':len(c.execute('PRAGMA foreign_key_check').fetchall()),
            'integrity':c.execute('PRAGMA integrity_check').fetchone()[0]}
