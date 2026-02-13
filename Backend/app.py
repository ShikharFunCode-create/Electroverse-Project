from flask import Flask, request, Response, jsonify
from pymongo import MongoClient
import gridfs
from bson.objectid import ObjectId
from datetime import datetime, timedelta
import json

app = Flask(__name__)

# 1. Connect to Mongo
client = MongoClient("mongodb://localhost:27017/")
db = client.video_storage_db
# This expires the 'metadata' entry after 7 days.
db.fs.files.create_index("uploadDate", expireAfterSeconds=604800)
fs = gridfs.GridFS(db)

# 2. upload limit (500MB)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024

def cleanup_orphaned_chunks():
    """Deletes data chunks that no longer have a parent file document due to TTL."""
    valid_ids = db.fs.files.distinct("_id")
    result = db.fs.chunks.delete_many({"files_id": {"$nin": valid_ids}})
    print(f"Cleanup: Removed {result.deleted_count} orphaned chunks.")

@app.route('/')
def home():
    return "Encrypted Video Server is Running"

@app.route('/upload', methods=['POST'])
def upload():
    if 'video' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    
    file = request.files['video']
    
    # Capture metadata from the encryption person's script
    # They are likely sending camera_id or other headers
    camera_id = request.form.get('camera_id', 'CAM_67') 
    
    # Store as 'application/octet-stream' because it is encrypted binary, not a playable mp4 yet
    file_id = fs.put(
        file, 
        filename=file.filename, 
        content_type='application/octet-stream',
        metadata={
            "camera_id": camera_id,
            "plate_numbers": [], # Initialize empty list for future OCR updates
            "is_encrypted": True,
            "container_format": "WattLagGyi"
        }
    )
    
    return jsonify({
        "video_id": str(file_id),
        "status": "stored_encrypted"
    }), 201

@app.route('/video/<video_id>')
def stream_video(video_id):
    try:
        video_file = fs.get(ObjectId(video_id))
        
        def generate():
            # Standard chunked streaming of the encrypted blob
            # The client (or a middleware) will need to decrypt this
            for chunk in video_file:
                yield chunk

        # We use octet-stream because the browser cannot play this directly 
        # until the 'encryption person's' logic decrypts it.
        return Response(generate(), mimetype='application/octet-stream')
    except Exception:
        return "Video not found", 404

@app.route('/update_plate/<video_id>', methods=['PATCH'])
def update_plate(video_id):
    data = request.get_json()
    plate_numbers = data.get('plate_numbers')

    if not plate_numbers:
        return jsonify({"error": "No plate number provided"}), 400

    result = db.fs.files.update_one(
        {"_id": ObjectId(video_id)},
        {"$push": {"metadata.plate_numbers": plate_numbers}}
    )

    if result.matched_count == 0:
        return jsonify({"error": "Video not found"}), 404

    return jsonify({"message": "Plate added to metadata"}), 200

@app.route('/search')
def search_videos():
    plate = request.args.get('plate')
    date_str = request.args.get('date') 
    camera_id = request.args.get('camera_id')

    query = {}

    if plate:
        query["metadata.plate_numbers"] = plate
    
    if camera_id:
        query["metadata.camera_id"] = camera_id

    if date_str:
        try:
            ist_start = datetime.strptime(date_str, '%Y-%m-%d')
            utc_start = ist_start - timedelta(hours=5, minutes=30)
            utc_end = utc_start + timedelta(days=1)
            query["uploadDate"] = {"$gte": utc_start, "$lt": utc_end}
        except ValueError:
            return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400

    cursor = db.fs.files.find(query)
    results = []
    
    for video in cursor:        
        utc_time = video['uploadDate']
        ist_time = utc_time + timedelta(hours=5, minutes=30)

        results.append({
            "video_id": str(video['_id']),
            "filename": video['filename'],
            "camera_id": video.get('metadata', {}).get('camera_id'),
            "upload_date_ist": ist_time.strftime('%Y-%m-%d %H:%M:%S'),
            "plates_found": video.get('metadata', {}).get('plate_numbers', [])
        })

    if not results:
        return jsonify({"message": "No results found"}), 404

    return jsonify(results), 200

if __name__ == '__main__':
    # Clean up any data left over from files that expired via TTL
    cleanup_orphaned_chunks()
    app.run(host='0.0.0.0', port=5000, debug=True)
