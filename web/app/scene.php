<?php
// シーン文 → 検索条件（APIキー未設定時・AIの上限を超えたときの簡易解釈）
// 副作用のない関数だけを置く（lib.php から読む。tests/test_routes.py が php-cli で検査する）
declare(strict_types=1);

// 景観の語（正規表現）。景観はシーン文に「最初に出てきた位置」の順に並べる
//  「港」：倉庫・コンテナ・埠頭・工場などの港は port_industrial、漁港・岸壁・堤防は sea。単独の「港」は port_industrial（2026-10-28）
function scene_group_words(): array {
    return [
        'urban_new' => '高層|再開発|オフィス|近代|ビル街|都会', 'old_town' => '路地|下町|古い街|商店街|細い道',
        'commercial' => '繁華街|飲み屋|ネオン|中洲|天神|賑やか', 'residential' => '住宅|団地|マンション|家の前',
        'institution' => '学校|病院|役所|図書館|大学', 'transport' => '駅|歩道橋|橋(?!脚)|高架橋',
        'rail' => '線路|踏切|電車の見え|列車|高架下|ガード下|鉄橋',
        'port_industrial' => '倉庫|工場|コンテナ|ふ頭|埠頭|港湾|(?<![漁空])港(?!湾)',
        'river' => '川|河川|河原|河川敷|運河|川沿い|川べり',
        'sea' => '海(?!水浴)|海辺|海岸|岸壁|堤防|防波堤|漁港|干潟|湾',
        'beach' => '砂浜|ビーチ|浜辺|海水浴|波打ち際',
        'lake' => '池|湖|ため池|溜池|貯水池|ダム湖|お堀|堀端',
        'water' => '水辺|水際',
        'green' => '公園|山|丘|森|緑', 'historic' => '神社|寺|歴史|城', 'vacant' => '廃墟|空き|廃校|閉鎖',
        'slope' => '坂道|坂の|坂を|急な坂|上り坂|下り坂|階段|石段|高低差|高台|見下ろ|見晴らし',
    ];
}

// 雰囲気の語 → タグ（段階2：データで測れるタグ night_ok・lonely・narrow_street などに結びつける）
function scene_tag_words(): array {
    return [
        'lonely' => '寂し|孤独|一人|ひとり|人気のない|誰もいない', 'low_traffic' => '人気のない|静か|誰もいない|一人|ひとり',
        'calm' => '静か|穏やか|のどか', 'lively' => '賑や|活気|人混み|雑踏', 'high_traffic' => '人混み|雑踏|人通りの多い',
        'nostalgic' => '懐かし|昭和|レトロ',
        'night_ok' => '夜|深夜|終電|夜の街', 'neon' => 'ネオン|飲み屋|歓楽街', 'dawn_ok' => '早朝|明け方', 'sunset_ok' => '夕方|夕景|夕日',
        'narrow_street' => '路地|細い道|狭い道|裏通り', 'wide_open' => '見下ろ|開けた|見晴らし|広々',
        'sea_view' => '海沿い|海辺|海を|海が見え', 'river_view' => '川沿い|川べり|川辺|河川敷',
        'skyline' => '高層|ビル群|摩天楼', 'industrial_structure' => '工場|倉庫|コンテナ|クレーン',
        'slope_stairs' => '坂|階段|石段', 'greenery' => '公園|緑|木々|森',
        'tense' => '緊張|追われ|逃げ', 'romantic' => '恋|デート|告白', 'futuristic' => '近未来|未来',
    ];
}

function interpret_by_keywords(string $scene): array {
    $hits = [];
    foreach (scene_group_words() as $g => $re) {
        if (preg_match("/($re)/u", $scene, $m, PREG_OFFSET_CAPTURE)) $hits[$g] = $m[0][1];
    }
    asort($hits);
    $tags = [];
    foreach (scene_tag_words() as $t => $re) if (preg_match("/($re)/u", $scene)) $tags[$t] = 1.0;
    $mode = preg_match('/(車|ドライブ|運転)/u', $scene) ? 'car' : (preg_match('/(歩|散歩)/u', $scene) ? 'walk' : 'any');
    return ['sequence' => array_slice(array_keys($hits), 0, 4), 'tags' => $tags ?: ['lonely' => 0.1],
            'travel_mode' => $mode, 'notes' => 'キーワードから簡易的に解釈しました'];
}
