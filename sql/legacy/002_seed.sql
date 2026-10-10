-- マスタ初期データ
insert into areas (code, name, stage) values
 ('fuk-chuo','中央区',1),('fuk-hakata','博多区',1),('fuk-higashi','東区',1),
 ('fuk-sawara','早良区',1),('fuk-nishi','西区',1),('fuk-minami','南区',1),('fuk-jonan','城南区',1)
on conflict do nothing;

insert into landscape_groups (code, name) values
 ('urban_new','新しい都市景観'),
 ('old_town','古い街並み・路地'),
 ('commercial','繁華街・商業'),
 ('residential','住宅地・団地'),
 ('institution','学校・病院・公共施設'),
 ('transport','交通インフラ・駅'),
 ('port_industrial','港湾・工業・倉庫'),
 ('water','河川・海・水辺'),
 ('green','公園・丘陵・自然'),
 ('historic','歴史・寺社'),
 ('vacant','空き施設・再開発地')
on conflict do nothing;

insert into spot_categories (code, name, landscape_group) values
 ('new_building','新築ビル・ホテル','urban_new'),
 ('public_space','公開空地・歩行者空間','urban_new'),
 ('alley','細街路・路地','old_town'),
 ('shotengai','商店街','old_town'),
 ('eatery_street','飲食店街','commercial'),
 ('commercial_facility','商業施設','commercial'),
 ('school','学校・大学','institution'),
 ('hospital','病院・福祉施設','institution'),
 ('civic','役所・ホール・図書館・文化施設','institution'),
 ('housing_estate','団地・古い集合住宅','residential'),
 ('residential','住宅街・新興住宅地','residential'),
 ('station','駅前・交通結節点','transport'),
 ('bridge_overpass','橋・高架・歩道橋','transport'),
 ('railway','線路沿い','transport'),
 ('factory','工場','port_industrial'),
 ('warehouse','倉庫・物流施設','port_industrial'),
 ('port','港湾・ふ頭・コンテナヤード','port_industrial'),
 ('river','河川・河川敷・運河','water'),
 ('coast','海岸・干潟','water'),
 ('fishing_port','漁港','water'),
 ('island','島','water'),
 ('park','公園・庭園・緑地','green'),
 ('hill','丘陵・山道・展望地点','green'),
 ('shrine_temple','寺社','historic'),
 ('heritage','歴史的建築・遺跡','historic'),
 ('vacant','空き店舗・閉鎖施設・旧工場','vacant'),
 ('redevelopment_site','解体・再開発予定地','vacant')
on conflict do nothing;

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
on conflict do nothing;
