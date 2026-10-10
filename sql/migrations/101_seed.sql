-- 101: マスタの初期値（種別・景観は locadb/categories.py と同じ。収集のたびに categories.py の内容で更新される）
insert into areas (code, name, stage, osm_relation) values
 ('fuk-chuo','中央区',1,4008554),
 ('fuk-hakata','博多区',1,4008575),
 ('fuk-higashi','東区',1,4008561),
 ('fuk-sawara','早良区',1,4008553),
 ('fuk-nishi','西区',1,4008548),
 ('fuk-minami','南区',1,4008564),
 ('fuk-jonan','城南区',1,4008545)
on conflict (code) do update set name=excluded.name, osm_relation=excluded.osm_relation;

insert into landscape_groups (code, name, color) values
 ('urban_new','新しい都市景観','#7A5BA6'),
 ('old_town','古い街並み・路地','#D27A2C'),
 ('commercial','繁華街・商業','#C8423A'),
 ('residential','住宅地・団地','#B9547F'),
 ('institution','学校・病院・公共施設','#4F5DA8'),
 ('transport','交通インフラ・駅','#2F8DA0'),
 ('port_industrial','港湾・工業・倉庫','#6B6F72'),
 ('river','川・河川','#3478C6'),
 ('sea','海・海辺','#1F4E8C'),
 ('beach','砂浜','#C49A45'),
 ('lake','池・湖','#2E8C7E'),
 ('green','公園・丘陵・自然','#4F8A4E'),
 ('historic','歴史・寺社','#8C6A4F'),
 ('vacant','空き施設・再開発地','#9C978E'),
 ('water','水辺（川・海・砂浜・池のどれでも）','#3478C6')
on conflict (code) do update set name=excluded.name, color=excluded.color;

insert into spot_categories (code, name, landscape_group, shape, spacing_m, priority) values
 ('station','駅前・交通結節点','transport','point',0,0),
 ('shrine_temple','寺社','historic','point',0,1),
 ('heritage','歴史的建築・遺跡','historic','point',0,2),
 ('school','学校・大学','institution','point',0,3),
 ('hospital','病院・福祉施設','institution','point',0,4),
 ('civic','役所・ホール・図書館・文化施設','institution','point',0,5),
 ('beach','砂浜・海水浴場','beach','point',0,6),
 ('fishing_port','漁港','sea','point',0,7),
 ('port','港湾・ふ頭・コンテナヤード','port_industrial','point',0,8),
 ('island','島','sea','point',0,9),
 ('pond','池・湖・ため池・お堀','lake','point',0,10),
 ('park','公園・庭園・緑地','green','point',0,11),
 ('hill','丘陵・山道・展望地点','green','point',0,12),
 ('bridge_overpass','橋・歩道橋','transport','point',0,13),
 ('commercial_facility','商業施設','commercial','point',0,14),
 ('eatery_street','飲食店街・市場・屋台','commercial','point',0,15),
 ('housing_estate','団地・古い集合住宅','residential','point',0,16),
 ('new_building','高層ビル・ホテル','urban_new','point',0,17),
 ('factory','工場','port_industrial','point',0,18),
 ('warehouse','倉庫・物流施設','port_industrial','point',0,19),
 ('vacant','空き店舗・閉鎖施設','vacant','point',0,20),
 ('redevelopment_site','工事中・再開発地','vacant','point',0,21),
 ('shotengai','商店街','old_town','line',150,22),
 ('alley','細街路・路地・階段','old_town','line',120,23),
 ('public_space','広場・歩行者空間','urban_new','line',150,24),
 ('river','河川・運河','river','line',400,25),
 ('coast','海岸・護岸・干潟','sea','line',400,26),
 ('railway','線路沿い','transport','line',600,27)
on conflict (code) do update set name=excluded.name, landscape_group=excluded.landscape_group, shape=excluded.shape, spacing_m=excluded.spacing_m, priority=excluded.priority;

insert into tags (code, name, facet) values
 ('lonely','寂しい','mood'),('lively','活気','mood'),('nostalgic','懐かしい','mood'),
 ('inorganic','無機質','mood'),('romantic','ロマンチック','mood'),('tense','緊張感','mood'),
 ('calm','穏やか','mood'),('gritty','荒々しい','mood'),('futuristic','近未来的','mood'),
 ('night_ok','夜間映え','time'),('dawn_ok','早朝映え','time'),('sunset_ok','夕景映え','time'),
 ('showa','昭和感','era'),('heisei','平成感','era'),('reiwa','現代的','era'),
 ('low_traffic','人通り少','density'),('high_traffic','人通り多','density'),
 ('sea_view','海が見える','feature'),('river_view','川が見える','feature'),
 ('skyline','高層ビル群','feature'),('narrow_street','狭い道','feature'),
 ('wide_open','開けた空間','feature'),('neon','ネオン・看板','feature'),
 ('streetlight_warm','暖色の街灯','feature'),('industrial_structure','クレーン・工業構造物','feature'),
 ('greenery','緑','feature'),('slope_stairs','坂・階段','feature')
on conflict (code) do update set name=excluded.name, facet=excluded.facet;
