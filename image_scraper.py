import os
import requests
from bs4 import BeautifulSoup
import time
import re
from urllib.parse import unquote, urlparse
import json
import gradio as gr
from PIL import Image
import io
import webview
import threading

# 定数の定義
ASPECT_RATIO_CHOICES = [
    "指定なし ⬜",
    "1:1 ⬛",
    "4:3 🔲",
    "16:9 📺",
    "9:16 📱"
]
IMAGE_FORMAT_CHOICES = ["webp", "jpg", "png"]
BASE_FOLDER = "img"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
TIMEOUT = 30  # リクエストのタイムアウト時間（秒）

# キャンセルフラグ
cancel_flag = threading.Event()

# ファイル名から特殊文字を除去する関数
def sanitize_filename(filename):
    return re.sub(r'[\\/*?:"<>|]', "", filename)

# 保存用フォルダを作成する関数
def create_folder(base_folder, query):
    sanitized_query = sanitize_filename(query)
    folder_path = os.path.join(base_folder, sanitized_query)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
    return folder_path

# アスペクト比を解析する関数
def parse_aspect_ratio(aspect_ratio):
    if aspect_ratio == "指定なし ⬜":
        return None
    match = re.search(r'(\d+):(\d+)', aspect_ratio)
    if match:
        return float(match.group(1)) / float(match.group(2))
    return None

# 画像をダウンロードし、指定された形式に変換する関数
def download_and_convert_image(url, folder, aspect_ratio, aspect_ratio_tolerance, image_format):
    if cancel_flag.is_set():
        return None
    try:
        response = requests.get(url, stream=True, timeout=TIMEOUT)
        if response.status_code == 200:
            content_type = response.headers.get('content-type', '').lower()
            if 'image' in content_type:
                image_data = response.content
                image = Image.open(io.BytesIO(image_data))
                
                # アスペクト比のチェック
                if aspect_ratio is not None:
                    width, height = image.size
                    image_ratio = width / height
                    if abs(image_ratio - aspect_ratio) > aspect_ratio_tolerance:
                        return None

                # 元のファイル名を取得
                parsed_url = urlparse(url)
                original_filename = os.path.basename(parsed_url.path)
                filename = sanitize_filename(original_filename)
                
                # 拡張子を変更
                filename_without_ext, _ = os.path.splitext(filename)
                filename = f"{filename_without_ext}.{image_format}"
                
                filepath = os.path.join(folder, filename)
                
                # 同名ファイルが存在する場合はスキップ
                if os.path.exists(filepath):
                    return None
                
                # 指定された形式で保存
                image.save(filepath, image_format.upper())
                return filepath
            else:
                return None
        else:
            return None
    except requests.Timeout:
        return None
    except Exception:
        return None

# 画像URLを取得する関数
def fetch_image_urls(search_url, headers):
    if cancel_flag.is_set():
        return []
    try:
        response = requests.get(search_url, headers=headers, timeout=TIMEOUT)
        if response.status_code != 200:
            return []
        
        soup = BeautifulSoup(response.text, 'html.parser')
        image_urls = []
        
        for img in soup.find_all('a', class_='iusc'):
            if cancel_flag.is_set():
                break
            try:
                m_content = json.loads(img.get('m', '{}'))
                img_url = m_content.get('murl')
                if img_url and img_url.startswith('http'):
                    image_urls.append(img_url)
            except json.JSONDecodeError:
                continue
            except Exception:
                continue
        
        return image_urls
    except requests.Timeout:
        return []
    except Exception:
        return []

# 画像をスクレイピングする主要な関数
def scrape_images(query, num_images=10, aspect_ratio="指定なし ⬜", aspect_ratio_tolerance=0.2, image_format="webp", progress=None):
    cancel_flag.clear()
    search_url = f"https://www.bing.com/images/search?q={query}&form=HDRSC2&first=1"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    
    if not os.path.exists(BASE_FOLDER):
        os.makedirs(BASE_FOLDER)
    folder = create_folder(BASE_FOLDER, query)
    
    image_urls = fetch_image_urls(search_url, headers)
    if not image_urls:
        return []
    
    downloaded_images = []
    
    target_ratio = parse_aspect_ratio(aspect_ratio)
    
    if progress is not None:
        progress(0, desc="画像をダウンロード中")
    
    for i, img_url in enumerate(image_urls):
        if cancel_flag.is_set():
            break
        if len(downloaded_images) >= num_images:
            break
        
        filepath = download_and_convert_image(img_url, folder, target_ratio, aspect_ratio_tolerance, image_format)
        if filepath:
            downloaded_images.append(filepath)
            if progress is not None:
                progress((len(downloaded_images)) / num_images, desc=f"{len(downloaded_images)}枚中{num_images}枚ダウンロード完了")
        
        time.sleep(1)  # 1秒待機してサーバーに負荷をかけないようにする

    return downloaded_images

# Gradio用の画像スクレイピング関数
def gradio_scrape_images(query, num_images, aspect_ratio, aspect_ratio_tolerance, image_format, progress=gr.Progress()):
    try:
        if not query.strip():
            raise ValueError("検索キーワードを入力してください。")
        if num_images < 1 or num_images > 50:
            raise ValueError("ダウンロードする画像の数は1から50の間で指定してください。")
        
        downloaded_images = scrape_images(query, num_images, aspect_ratio, aspect_ratio_tolerance, image_format, progress)
        if not downloaded_images:
            if cancel_flag.is_set():
                raise gr.Error("ダウンロードがキャンセルされました。")
            else:
                raise gr.Error("画像のダウンロードに失敗しました。")
        return downloaded_images
    except Exception as e:
        raise gr.Error(str(e))

# ダウンロードをキャンセルする関数
def cancel_download():
    cancel_flag.set()
    return "ダウンロードをキャンセルしました。"

# 入力をリセットする関数
def reset_inputs():
    return ["", 10, "指定なし ⬜", 0.2, "webp"]

# Gradioインターフェースの定義
with gr.Blocks() as iface:
    gr.Markdown("# がぞうとってくる～ん！")
    gr.Markdown("キーワードを入力すると、関連する画像を自動的にダウンロードして表示しますなん。")
    
    with gr.Row():
        with gr.Column():
            query = gr.Textbox(label="検索したい画像のキーワードを入力してください")
            num_images = gr.Slider(minimum=1, maximum=50, value=10, step=1, label="ダウンロードする画像の数なん！")
            aspect_ratio = gr.Dropdown(choices=ASPECT_RATIO_CHOICES, value="指定なし ⬜", label="アスペクト比")
            aspect_ratio_tolerance = gr.Slider(minimum=0.1, maximum=0.5, value=0.2, step=0.1, label="アスペクト比の許容範囲")
            image_format = gr.Dropdown(choices=IMAGE_FORMAT_CHOICES, value="webp", label="画像の保存形式")
            
            with gr.Row():
                submit_btn = gr.Button("がぞうとってくるん！")
                cancel_btn = gr.Button("とるのやめるん！")
                clear_btn = gr.Button("設定クリアなん！")
        
        with gr.Column():
            output_gallery = gr.Gallery(label="ダウンロードされた画像なん！")
            output_text = gr.Textbox(label="メッセージなん！")
    
    submit_btn.click(fn=gradio_scrape_images, 
                     inputs=[query, num_images, aspect_ratio, aspect_ratio_tolerance, image_format], 
                     outputs=output_gallery)
    
    cancel_btn.click(fn=cancel_download, 
                     inputs=None, 
                     outputs=output_text)
    
    clear_btn.click(fn=reset_inputs, 
                    inputs=None, 
                    outputs=[query, num_images, aspect_ratio, aspect_ratio_tolerance, image_format])

# Gradioを実行する関数
def run_gradio():
    iface.launch(share=True)

# WebViewを実行する関数
def run_webview():
    webview.create_window("がぞうとってくる～ん！", "http://127.0.0.1:7860")
    webview.start()

if __name__ == "__main__":
    # Gradioを別スレッドで実行
    gradio_thread = threading.Thread(target=run_gradio)
    gradio_thread.start()
    
    # Gradioサーバーが起動するのを少し待つ
    time.sleep(5)
    
    # WebViewを実行
    run_webview()