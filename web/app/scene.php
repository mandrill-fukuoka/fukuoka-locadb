<?php
// シーン文 → 検索条件（APIキー未設定時・AIの上限を超えたときの簡易解釈）
// 副作用のない関数だけを置く（lib.php から読む。tests/test_routes.py が php-cli で検査する）
declare(strict_types=1);

// 景観の語（正規表現）。景観はシーン文に「最初に出てきた位置」の順に並べる
//  「港」：倉庫・コンテナ・埠頭・工場などの港は port_industrial、漁港・岸壁・堤防は sea。単独の「港」は port_industrial（2026-10-28）
function scene_group_words(): array {
    return [
        'urban_new' => '高層|オフィス|近代|ビル街|都会|広場', 'old_town' => '路地|下町|古い街|商店街|細い道',
        'commercial' => '繁華街|飲み屋|ネオン|中洲|天神|賑やか|屋台|歓楽街|飲み屋街|夜の街', 'residential' => '住宅|団地|マンション|家の前',
        'institution' => '学校|高校|中学|校舎|校門|病院|役所|図書館|大学', 'transport' => '駅|歩道橋|橋(?!脚)|高架橋|高架(?!下)|高速道路|高速の|都市高速|バス停',
        'rail' => '線路|踏切|電車の見え|列車|高架下|ガード下|鉄橋', 'bath' => '温泉|銭湯|公衆浴場|スーパー銭湯|湯治|露天風呂',
        'port_industrial' => '倉庫|工場|コンテナ|ふ頭|埠頭|港湾|フェリー|桟橋|(?<![漁空])港(?!湾)',
        'river' => '川|河川|河原|河川敷|運河|川沿い|川べり|土手|河川敷|河畔',
        'sea' => '海(?!水浴)|海辺|海岸|岸壁|堤防|防波堤|漁港|干潟|(?<!港)湾',
        'beach' => '砂浜|ビーチ|浜辺|海水浴|波打ち際',
        'lake' => '池|湖|ため池|溜池|貯水池|ダム湖|お堀|堀端',
        'water' => '水辺|水際',
        'green' => '公園|山|丘|森|緑|展望台', 'historic' => '神社|寺|歴史|城|石垣|祭り|参道', 'vacant' => '廃墟|空き|廃校|閉鎖|廃線|廃工場|廃屋|廃ビル|再開発|工事現場|更地',
        'slope' => '坂道|坂の|坂を|急な坂|上り坂|下り坂|階段|石段|高低差|高台|見下ろ|見晴らし',
    ];
}

// 雰囲気の語 → タグ（段階2：データで測れるタグ night_ok・lonely・narrow_street などに結びつける）
function scene_tag_words(): array {
    return [
        'lonely' => '寂し|孤独|一人|ひとり|人気のない|誰もいない', 'low_traffic' => '人気のない|静か|誰もいない|一人|ひとり',
        'calm' => '静か|穏やか|のどか', 'lively' => '賑や|活気|人混み|雑踏', 'high_traffic' => '人混み|雑踏|人通りの多い',
        'nostalgic' => '懐かし|昭和|レトロ',
        'night_ok' => '夜|深夜|終電|夜の街|祭り', 'neon' => 'ネオン|飲み屋|歓楽街', 'dawn_ok' => '早朝|明け方', 'sunset_ok' => '夕方|夕景|夕日|夕暮れ|黄昏',
        'narrow_street' => '路地|細い道|狭い道|裏通り', 'wide_open' => '見下ろ|開けた|見晴らし|広々',
        'sea_view' => '海沿い|海辺|海を|海が見え', 'river_view' => '川沿い|川べり|川辺|河川敷',
        'skyline' => '高層|ビル群|摩天楼', 'industrial_structure' => '工場|倉庫|コンテナ|クレーン',
        'slope_stairs' => '坂|階段|石段', 'greenery' => '公園|緑|木々|森',
        'tense' => '緊張|追われ|追っ|逃げ|容疑者|刑事|張り込|取引|死体|事件', 'romantic' => '恋|デート|告白|二人', 'futuristic' => '近未来|未来',
    ];
}

// 福岡市の地名（区の判定つき）。景観の語と同じ字を含む地名（川端・山笠・城南・大橋・柳橋など）が景観に誤反応しないよう、
// 景観の判定の前に伏せる。地名は区の絞り込み（起点の区）に回す。値：[区コード, 景観が他に読めないときだけ使う景観 or null]
// 区が確かなものだけを入れる（誤ると検索が狭まるため）。育てる場合も、区が複数にまたがる地名・広域の地名（博多湾など）は入れない。
function scene_place_names(): array {
    $chuo = 'fuk-chuo'; $hakata = 'fuk-hakata'; $higashi = 'fuk-higashi'; $sawara = 'fuk-sawara'; $nishi = 'fuk-nishi'; $minami = 'fuk-minami'; $jonan = 'fuk-jonan';
    return [
        '西中洲' => [$chuo, 'commercial'], '中洲' => [$hakata, 'commercial'], '天神' => [$chuo, 'commercial'],
        '大名' => [$chuo, null], '今泉' => [$chuo, null], '赤坂' => [$chuo, null], '薬院' => [$chuo, null], '渡辺通' => [$chuo, null],
        '春吉' => [$chuo, null], '大濠' => [$chuo, null], '舞鶴' => [$chuo, null], '荒戸' => [$chuo, null], '唐人町' => [$chuo, null],
        '六本松' => [$chuo, null], '警固' => [$chuo, null], '桜坂' => [$chuo, null], '平尾' => [$chuo, null], '長浜' => [$chuo, null],
        '川端' => [$hakata, null], '祇園' => [$hakata, null], '呉服町' => [$hakata, null], '御供所' => [$hakata, null], '櫛田' => [$hakata, null],
        '冷泉' => [$hakata, null], '吉塚' => [$hakata, null], '山笠' => [null, null],
        '箱崎' => [$higashi, null], '香椎' => [$higashi, null], '千早' => [$higashi, null], '名島' => [$higashi, null], '和白' => [$higashi, null],
        '西新' => [$sawara, null], '百道' => [$sawara, null], '藤崎' => [$sawara, null], '室見' => [$sawara, null],
        '姪浜' => [$nishi, null], '今宿' => [$nishi, null], '元岡' => [$nishi, null],
        '高宮' => [$minami, null], '井尻' => [$minami, null], '大橋' => [$minami, null], '柳橋' => [$chuo, null],
        '七隈' => [$jonan, null], '別府' => [$jonan, null], '鳥飼' => [$jonan, null], '城南' => [$jonan, null],
    ];
}

// 地名を伏せる。返り値：[伏せた文, 見つかった地名の一覧（名前・区・景観・出現位置）]。長い名前を先に伏せる（西中洲 → 中洲 の順）
function scene_mask_places(string $scene): array {
    $names = array_keys(scene_place_names());
    usort($names, fn($a, $b) => mb_strlen($b) <=> mb_strlen($a));
    $found = [];
    foreach ($names as $n) {
        $pos = mb_strpos($scene, $n);
        if ($pos === false) continue;
        [$area, $grp] = scene_place_names()[$n];
        $found[] = ['name' => $n, 'area' => $area, 'group' => $grp, 'pos' => $pos];
        $scene = str_replace($n, str_repeat('◆', mb_strlen($n)), $scene);
    }
    usort($found, fn($a, $b) => $a['pos'] <=> $b['pos']);
    return [$scene, $found];
}

// シーン文の頭に出てくる地名から、起点の区を決める（区が1つに決まるときだけ。景観の語より後ろの地名は使わない）
//   地名が出てくる位置は伏せた文での位置。景観の語の最初の位置より前にある地名だけを使う
function scene_area_hint(string $scene): ?string {
    [$masked, $found] = scene_mask_places($scene);
    if (!$found) return null;
    $first = null;
    foreach (scene_group_words() as $re) {
        if (preg_match("/($re)/u", $masked, $m, PREG_OFFSET_CAPTURE)) {
            $at = mb_strlen(substr($masked, 0, $m[0][1]));
            $first = $first === null ? $at : min($first, $at);
        }
    }
    $areas = [];
    foreach ($found as $f) {
        if ($f['area'] === null) continue;
        if ($first !== null && $f['pos'] > $first) continue;
        $areas[$f['area']] = 1;
    }
    return count($areas) === 1 ? (string)array_key_first($areas) : null;
}

function interpret_by_keywords(string $scene): array {
    [$masked, $places] = scene_mask_places($scene);
    $hits = [];
    foreach (scene_group_words() as $g => $re) {
        if (preg_match("/($re)/u", $masked, $m, PREG_OFFSET_CAPTURE)) $hits[$g] = $m[0][1];
    }
    // 景観の語が1つも読めないときだけ、地名に結びつく景観（天神・中洲 → 繁華街）を使う
    if (!$hits) foreach ($places as $p) if ($p['group']) { $hits[$p['group']] = 0; break; }
    asort($hits);
    $tags = [];
    foreach (scene_tag_words() as $t => $re) if (preg_match("/($re)/u", $masked)) $tags[$t] = 1.0;
    // 否定：「人のいない」「人通りの少ない」「賑わいのない」は、賑やか・人通りの多いの逆（人が少ない）
    if (preg_match('/(人|人通り|人影|人けも?|賑わい|活気)(が|は|の)?(いない|おらず|少な|ほとんどいな|ない|なく)|閑散|ひっそり|さびれ/u', $masked)) {
        unset($tags['lively'], $tags['high_traffic']);
        $tags['lonely'] = 1.0; $tags['low_traffic'] = 1.0;
    }
    // 時間帯：早朝・明け方・深夜は、人が少ない（夜の賑わいの語がなければ）
    if (preg_match('/早朝|明け方|未明|夜明け前|深夜|真夜中/u', $masked) && !isset($tags['lively']) && !isset($tags['high_traffic'])) $tags['low_traffic'] = 1.0;
    // 「自転車」「電車」「列車」の「車」は車の移動にしない
    $mode = preg_match('/((?<!自転|電|列|汽|屋台|下)車|ドライブ|運転)/u', $masked) ? 'car'
          : (preg_match('/(歩|散歩|自転車)/u', $masked) ? 'walk' : 'any');
    $area = scene_area_hint($scene);
    $notes = 'キーワードから簡易的に解釈しました';
    if ($area) {
        $label = ['fuk-chuo' => '中央区', 'fuk-hakata' => '博多区', 'fuk-higashi' => '東区', 'fuk-sawara' => '早良区', 'fuk-nishi' => '西区', 'fuk-minami' => '南区', 'fuk-jonan' => '城南区'][$area] ?? '';
        $notes .= '（地名から起点を' . $label . 'に絞りました）';
    }
    return ['sequence' => array_slice(array_keys($hits), 0, 4), 'tags' => $tags ?: ['lonely' => 0.1],
            'travel_mode' => $mode, 'area' => $area, 'notes' => $notes];
}
