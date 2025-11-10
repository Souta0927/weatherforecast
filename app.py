import openmeteo_requests
import pandas as pd
import requests_cache
from retry_requests import retry
from flask import Flask, render_template, request
import os
import json # JSONモジュールを追加

# Flaskアプリケーションの初期化
app = Flask(__name__)

# --- グローバル変数: 47都道府県の座標とソート情報を格納するリスト ---
PREFECTURE_DATA_LIST = [] 
CSV_FILE_PATH = 'pref.csv' 


# --- アプリケーション起動時にCSVを読み込む関数 ---
def load_prefecture_coords():
    """CSVファイルを読み込み、PREFECTURE_DATA_LISTを初期化する"""
    global PREFECTURE_DATA_LIST
    PREFECTURE_DATA_LIST = []
    
    # 失敗時のデフォルト座標 (東京)
    DEFAULT_COORDS = {"latitude": 35.689, "longitude": 139.692}
    DEFAULT_KEY = "データ読み込みエラー発生（東京を使用）"

    if not os.path.exists(CSV_FILE_PATH):
        print(f"エラー: CSVファイルが見つかりません: {CSV_FILE_PATH}")
        PREFECTURE_DATA_LIST.append({
            "display_name": DEFAULT_KEY,
            "latitude": DEFAULT_COORDS['latitude'], 
            "longitude": 139.692,
            "region": "関東",
        })
        return

    try:
        # PandasでCSVを読み込む。地方の欠損値を空文字列で埋める
        df = pd.read_csv(CSV_FILE_PATH).fillna({'地方': ''}) 
        
        # 緯度・経度から '∘' 記号を削除し、float型に変換する
        df['緯度_num'] = df['緯度（北緯 N）'].astype(str).str.replace('∘', '').astype(float)
        df['経度_num'] = df['経度（東経 E）'].astype(str).str.replace('∘', '').astype(float)
        
        # 必要な列が揃っている行のみを処理
        df = df.dropna(subset=['都道府県', '県庁所在地', '緯度_num', '経度_num'])
        
        for index, row in df.iterrows():
            location_name = row['県庁所在地']
            prefecture_name = row['都道府県']
            region_name = row['地方'] if row['地方'] else 'その他' # 地方が空の場合は'その他'とする
            
            # ドロップダウンで表示する名称を「都道府県（県庁所在地）」形式に調整
            if prefecture_name == '東京都':
                display_name = '東京都庁（新宿区）'
            elif prefecture_name == '北海道':
                display_name = '北海道庁（札幌市）'
            else:
                display_name = f"{prefecture_name}（{location_name}）"
            
            # リストに辞書として格納
            PREFECTURE_DATA_LIST.append({
                "display_name": display_name,
                "latitude": row['緯度_num'],
                "longitude": row['経度_num'],
                "region": region_name,
            })
            
        print(f"CSVから{len(PREFECTURE_DATA_LIST)}件の座標データをロードしました。")

    except Exception as e:
        print(f"CSVファイルの読み込み中にエラーが発生しました: {e}")
        PREFECTURE_DATA_LIST = [{
            "display_name": DEFAULT_KEY,
            "latitude": DEFAULT_COORDS['latitude'], 
            "longitude": DEFAULT_COORDS['longitude'],
            "region": "エラー",
        }]

# --- 気象データ取得ロジック（新しい変数に対応） ---
def get_weather_data(latitude, longitude, location_name):
    # Setup the Open-Meteo API client with cache and retry on error
    cache_session = requests_cache.CachedSession('.cache', expire_after = 3600)
    retry_session = retry(cache_session, retries = 5, backoff_factor = 0.2)
    openmeteo = openmeteo_requests.Client(session = retry_session)

    url = "https://api.open-meteo.com/v1/forecast"
    
    # ユーザーが指定した10変数を設定
    params = {
        "latitude": latitude, 
        "longitude": longitude, 
        "daily": [
            "temperature_2m_max", "temperature_2m_min", "rain_sum", 
            "showers_sum", "daylight_duration", "sunshine_duration", 
            "sunrise", "sunset", "precipitation_probability_max", 
            "precipitation_sum"
        ], 
        "timezone": "Asia/Tokyo", 
    }
    responses = openmeteo.weather_api(url, params=params)

    response = responses[0]
    timezone_str = response.Timezone().decode('utf-8')
    
    # --- 日ごとのデータ処理 ---
    daily = response.Daily()
    
    # 変数の取得順序は params の daily リストと同じである必要があります (0-9のインデックス)
    daily_temperature_2m_max = daily.Variables(0).ValuesAsNumpy()
    daily_temperature_2m_min = daily.Variables(1).ValuesAsNumpy()
    daily_rain_sum = daily.Variables(2).ValuesAsNumpy()
    daily_showers_sum = daily.Variables(3).ValuesAsNumpy()
    daily_daylight_duration = daily.Variables(4).ValuesAsNumpy()
    daily_sunshine_duration = daily.Variables(5).ValuesAsNumpy()
    daily_sunrise = daily.Variables(6).ValuesInt64AsNumpy()
    daily_sunset = daily.Variables(7).ValuesInt64AsNumpy()
    daily_precipitation_probability_max = daily.Variables(8).ValuesAsNumpy()
    daily_precipitation_sum = daily.Variables(9).ValuesAsNumpy()


    # 日付範囲の生成 (タイムゾーン付き)
    date_range_tz_aware = pd.date_range(
        start = pd.to_datetime(daily.Time(), unit = "s", utc = True).tz_convert(timezone_str),
        end = pd.to_datetime(daily.TimeEnd(), unit = "s", utc = True).tz_convert(timezone_str),
        freq = pd.Timedelta(seconds = daily.Interval()),
        inclusive = "left"
    )
    
    # タイムゾーン情報を除去
    daily_data = {"日付": date_range_tz_aware.tz_localize(None)} 

    # 日の出と日の入りをJSTのHH:MM形式に変換
    daily_sunrise_dt = pd.to_datetime(daily_sunrise, unit='s').tz_localize('UTC').tz_convert(timezone_str).strftime('%H:%M')
    daily_sunset_dt = pd.to_datetime(daily_sunset, unit='s').tz_localize('UTC').tz_convert(timezone_str).strftime('%H:%M')

    # 日照時間、日照時間の単位を秒から時間に変換
    daily_daylight_hours = daily_daylight_duration / 3600
    daily_sunshine_hours = daily_sunshine_duration / 3600

    # データを辞書に追加
    daily_data["最高気温 (°C)"] = daily_temperature_2m_max.round(1)
    daily_data["最低気温 (°C)"] = daily_temperature_2m_min.round(1)
    daily_data["安定した降雨量 (mm)"] = daily_rain_sum.round(1) # rain_sum
    daily_data["にわか雨量 (mm)"] = daily_showers_sum.round(1) # showers_sum
    daily_data["昼光時間 (時間)"] = daily_daylight_hours.round(1)
    daily_data["日照時間 (時間)"] = daily_sunshine_hours.round(1)
    daily_data["日の出時刻"] = daily_sunrise_dt
    daily_data["日の入り時刻"] = daily_sunset_dt
    daily_data["降水確率 最大 (%)"] = daily_precipitation_probability_max.round(0).astype(int) 
    daily_data["降水合計 (mm)"] = daily_precipitation_sum.round(1) # precipitation_sum
    
    
    daily_dataframe = pd.DataFrame(data = daily_data)
    
    # --- 日ごとの詳細テーブル（daily_table）の整形とHTML変換 ---
    # 表示から除外する列をリストアップ
    columns_to_drop_from_daily = [
        "安定した降雨量 (mm)",
        "にわか雨量 (mm)",
        "昼光時間 (時間)",
        "日照時間 (時間)",
    ]
    
    # daily_dataframeのコピーを作成し、不要な列を削除
    daily_display_df = daily_dataframe.drop(columns=columns_to_drop_from_daily, errors='ignore')

    # --- カスタム分析テーブルの生成 ---
    # 既存のDataFrameから日付、降水合計、昼光時間、日照時間を取得
    custom_df = daily_dataframe[['日付', '降水合計 (mm)', '昼光時間 (時間)', '日照時間 (時間)']].copy()
    
    # 【追加】日照率 (日照時間 / 昼光時間) の計算（判定ロジックでのみ使用）
    custom_df['日照率'] = (
        custom_df.apply(
            lambda row: (row['日照時間 (時間)'] / row['昼光時間 (時間)']) if row['昼光時間 (時間)'] > 0 else 0,
            axis=1
        ) * 100
    )

    # 【判定】天気予報ロジックの更新
    
    # デフォルトを「🌞 晴れ」とする
    custom_df['天気予報'] = '🌞 晴れ'
    
    # 判定用のフラグ
    is_low_sunshine = custom_df['日照率'] < 50.0  # 日照率50%未満
    # 【NEW】晴れ時々曇り判定用のフラグ (50% <= 日照率 <= 90%)
    is_partly_cloudy = (custom_df['日照率'] >= 50.0) & (custom_df['日照率'] <= 90.0)
    # 降水なしのフラグ
    is_no_precip = custom_df['降水合計 (mm)'] == 0.0

    # 1. 降水注意 (最優先: 5.0mm以上)
    is_rain_warning = custom_df['降水合計 (mm)'] >= 5.0
    custom_df.loc[is_rain_warning, '天気予報'] = '☔ 雨注意' # 👈 修正: 絵文字追加

    # 2. 曇り時々雨 (次に優先: 日照率50%未満かつ 0mm < 降水 < 5.0mm)
    is_light_precip = (custom_df['降水合計 (mm)'] > 0.0) & (custom_df['降水合計 (mm)'] < 5.0)

    is_occasional_rain = is_low_sunshine & is_light_precip & (~is_rain_warning)
    custom_df.loc[is_occasional_rain, '天気予報'] = '☁️ 曇り時々雨' # 👈 修正: 絵文字追加
    
    # 3. 【NEW】晴れ時々曇り (降水 0.0mm かつ 50% <= 日照率 <= 90%)
    is_partly_cloudy_final = is_no_precip & is_partly_cloudy
    # 雨注意や時々雨に該当しない行にのみ適用
    custom_df.loc[is_partly_cloudy_final & (~is_rain_warning) & (~is_occasional_rain), '天気予報'] = '🌤️ 晴れ時々曇り' # 👈 修正: 絵文字追加
    
    # 4. 曇り (降水 0.0mm かつ 日照率50%未満)
    is_cloudy = is_no_precip & is_low_sunshine
    # 上記1, 2, 3の判定が適用されていない行にのみ適用
    custom_df.loc[is_cloudy & (~is_rain_warning) & (~is_occasional_rain) & (~is_partly_cloudy_final), '天気予報'] = '☁️ 曇り' # 👈 修正: 絵文字追加

    # 表示用のDataFrameを整形
    custom_df['日付'] = custom_df['日付'].dt.strftime('%m/%d') # 日付をMM/DD形式に
    custom_df = custom_df.rename(columns={
        '降水合計 (mm)': '降水量 (mm)',
        '昼光時間 (時間)': '昼光時間 (時間)',
        '日照時間 (時間)': '日照時間 (時間)',
    })
    
    # 【修正】日照率の計算列は表示から削除
    custom_df = custom_df.drop(columns=['日照率'])
    
    # 【修正】最終的なカスタム分析テーブルの列順を確定
    custom_df = custom_df[['日付', '降水量 (mm)', '昼光時間 (時間)', '日照時間 (時間)', '天気予報']]
    
    # 任意の表をHTMLに変換
    custom_analysis_table = custom_df.to_html(
        index=False, 
        float_format='%.1f', 
        classes='custom-table', # 新しいCSSクラスを指定
    )
    
    # --- チャートデータ生成 ---
    # 日付ラベルを 'MM/DD' 形式にフォーマット
    chart_labels = daily_data["日付"].strftime('%m/%d').tolist()
    
    chart_data = {
        "labels": chart_labels,
        "t_max": daily_temperature_2m_max.round(1).tolist(),
        "t_min": daily_temperature_2m_min.round(1).tolist(),
        # 降水合計 (mm) のデータをリストとして追加
        "precipitation_sum": daily_precipitation_sum.round(1).tolist(), 
    }
    chart_data_json = json.dumps(chart_data) # JSONにシリアライズ

    return {
        "location_name": location_name,
        "location": f"{latitude}°N, {longitude}°E",
        "elevation": f"{response.Elevation()} m asl",
        "timezone_info": timezone_str,
        # hourly_tableは空のHTMLコメントとして残しておく
        "hourly_table": "",
        "daily_table": daily_display_df.to_html(index=False, float_format='%.1f', classes='weather-table'),
        "chart_data_json": chart_data_json, # チャート用JSONデータを追加
        "custom_analysis_table": custom_analysis_table # 【変更】カスタム分析テーブル
    }

# Flaskのルート設定
@app.route('/', methods=['GET', 'POST'])
def index():
    # アプリケーション起動時のロード確認
    if not PREFECTURE_DATA_LIST:
        load_prefecture_coords()
        
    if not PREFECTURE_DATA_LIST:
        return "エラー: 県庁所在地の座標データがロードされていません。CSVファイルの内容を確認してください。", 500

    # 1. デフォルトの初期設定
    default_data = PREFECTURE_DATA_LIST[0]
    location_key = default_data['display_name']
    
    # 2. ソート順の決定 (ソートオプションは削除されたため、緯度降順に固定)
    sort_by = 'latitude_desc' 
    
    # 3. データのソート (緯度降順に固定)
    sorted_list = sorted(
        PREFECTURE_DATA_LIST, 
        key=lambda x: x['latitude'], 
        reverse=True # 緯度降順 (北から南)
    )

    # 4. 選択されたロケーションキーの処理
    if request.method == 'POST':
        location_key = request.form.get('prefecture_select')
        # 選択キーが有効なリスト内に存在するかを確認する
        valid_keys = [item['display_name'] for item in PREFECTURE_DATA_LIST]
        if location_key not in valid_keys:
            location_key = default_data['display_name']
    
    # 5. 現在選択されているロケーションの座標を取得
    selected_data = next(
        (item for item in PREFECTURE_DATA_LIST if item['display_name'] == location_key), 
        default_data
    )
    
    # 気象データを取得
    weather_data = get_weather_data(
        selected_data['latitude'], 
        selected_data['longitude'], 
        selected_data['display_name']
    )
    
    # HTMLテンプレートに渡すデータ
    weather_data["prefecture_data"] = sorted_list # ソートされたリストを渡す
    weather_data["selected_location"] = selected_data['display_name']
    weather_data["current_sort"] = sort_by # 現在のソート状態を渡す
    
    return render_template('index.html', **weather_data)

# アプリケーションをローカルサーバーで実行
if __name__ == '__main__':
    # アプリケーション起動時に座標データをロード
    load_prefecture_coords()

    if PREFECTURE_DATA_LIST:
        port = int(os.environ.get("PORT", 5000))  # Render用ポート指定
        app.run(host='0.0.0.0', port=port, debug=False)
    else:
        print("致命的なエラー: 座標データがロードされなかったため、Flaskアプリケーションを起動できません。")