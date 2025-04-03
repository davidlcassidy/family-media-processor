import calendar
import json
import os
import re
import shutil
import subprocess
import sys
import yaml
from datetime import timedelta, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from flask import Flask, render_template, request, Response, stream_with_context, jsonify

app = Flask(__name__)

# Internal Directories
media_directory = "/media"
move_to_directory = "/moveTo"

# Environment Variables
FAMILY_LAST_NAME = os.getenv("FAMILY_LAST_NAME", "Smith")    
APP_NAME = os.getenv("APP_NAME", f"{FAMILY_LAST_NAME} Family Media Processor")
GEOTAG_DATA_FILE = os.getenv("GEOTAG_DATA_FILE", "./config/geotag_data.yaml")
EXTERNAL_MEDIA_DIR = os.getenv("EXTERNAL_MEDIA_DIR", media_directory)
EXTERNAL_MOVE_TO_DIR = os.getenv("EXTERNAL_MOVE_TO_DIR", move_to_directory)
EXCLUDED_DIRECTORIES = os.getenv("EXCLUDED_DIRECTORIES", "").split(',')
FILES_TO_DELETE = os.getenv("FILES_TO_DELETE", "").split(',')
TZ = os.getenv("TZ", "GMT")
ENABLE_MOVE_FILES = os.getenv("ENABLE_MOVE_FILES", "false").lower() == "true"
VERBOSE_LOGGING = os.getenv("VERBOSE_LOGGING", "false").lower() == "true"

# Local Variables
tag_delimiter = ";"
tag_hierarchy_delimiter = "."
family_name = f"{FAMILY_LAST_NAME} Family"
copyright_notice = f"{family_name} Photos"
gps_coordinates_round_digits = 5  # Some software seems to struggle with longer gps coordinates
file_extension_whitelist = ['.jpg', '.jpeg', '.mp4']
extension_conversions = {".jpeg": ".jpg",}


@app.route('/')
def index():
    return render_template('index.html', app_name=APP_NAME, move_to_dir=EXTERNAL_MOVE_TO_DIR, move_files_enabled=ENABLE_MOVE_FILES)
    
@app.route('/directory-structure', methods=['GET'])
def directory_structure():
    def build_tree(root_path):
    	
        directory_name = os.path.basename(root_path)
        if directory_name in EXCLUDED_DIRECTORIES:
            return None
            
        tree = {
            'name': directory_name,
            'internal_path': root_path,
            'external_path': root_path.replace(media_directory, EXTERNAL_MEDIA_DIR, 1),
            'subdirectories': []
        }
        
        try:
            for entry in os.scandir(root_path):
                if entry.is_dir():
                    sub_tree = build_tree(entry.path)
                    if sub_tree:
                        tree['subdirectories'].append(sub_tree)
        except PermissionError:
            pass  # Skip directories we can't access
        return tree

    directory_tree = build_tree(media_directory)
    if directory_tree:
        directory_tree['name'] = EXTERNAL_MEDIA_DIR
    return jsonify(directory_tree)
    
@app.route('/geotag-data', methods=['GET'])
def geotag_data():
    try:
        with open(GEOTAG_DATA_FILE, 'r', encoding='utf-8') as file:
            return jsonify(yaml.safe_load(file))
    except Exception as e:
        return jsonify({"error": f"Error loading geotag data: {str(e)}"}), 500

@app.route('/start-processing', methods=['POST'])
def start_processing():
    data = request.get_json()

    if data['geotag_enabled']:
        try:
            latitude, longitude = map(float, data['geotag_data']['coordinates'].split(','))
            latitude = round(latitude, gps_coordinates_round_digits)
            longitude = round(longitude, gps_coordinates_round_digits)
        except ValueError:
            return "Error: Invalid coordinates format. Ensure they are number pairs.", 400

        country, country_code = data['geotag_data']['country'].split(' - ', 1)

        data['geotag_data'] = {
            "location": data['geotag_data']['location'],
            "city": data['geotag_data']['city'],
            "state": data['geotag_data']['state'],
            "country": country,
            "country_code": country_code,
            "longitude": longitude,
            "latitude": latitude,
        }

    return Response(stream_with_context(process_photos_stream(data)), mimetype='text/plain')


def process_photos_stream(data):
	
    # Validate env variables
    if not is_valid_timezone(TZ):
        yield f"Invalid Environment Variables: TZ={TZ}\n"
        yield f"{APP_NAME} ending early\n"
        return

    # Select files based on recursion mode
    file_items = []
    internal_selected_media_directory = data['selected_media_directory'].replace(EXTERNAL_MEDIA_DIR, media_directory);
    if data['recursive_search']:
        yield "RECURSIVE_SEARCH is true. Processing files in all subdirectories.\n"
        for root, _, files in os.walk(internal_selected_media_directory):
            file_items.extend(os.path.join(root, file) for file in files)
    else:
        yield "RECURSIVE_SEARCH is false. Processing files in the top-level directory only.\n"
        file_items = [os.path.join(internal_selected_media_directory, f) for f in os.listdir(internal_selected_media_directory) if os.path.isfile(os.path.join(internal_selected_media_directory, f))]

    # Log user options
    if ENABLE_MOVE_FILES:
        if data['move_files_selected']:
            yield "MOVE_FILES is true. Files will be moved.\n"
        else:
            yield "MOVE_FILES is false. Files will not be moved.\n"
    if data['geotag_enabled']:
        override_status = "enabled" if data['geotag_override'] else "disabled"
        yield f"GEOTAG_FILES is true. Files will be geotagged. (Override: {override_status})\n"
    else:
        yield "GEOTAG_FILES is false. Files will not be geotaged.\n"
        
        
    yield "-------------- New Process --------------\n"
    
    if not file_items:
        yield f"No files found in: {data['selected_media_directory']}.\n"
        yield f"{APP_NAME} ending early\n"
        return

    # Process each file
    for i, file_path in enumerate(file_items):
        file_name = os.path.basename(file_path)
        file_base_name, file_extension = os.path.splitext(file_name)
        
        # Check if file needs to be deleted
        if file_name in FILES_TO_DELETE:
            try:
                os.remove(file_path)
                yield f"File deleted: {file_name}\n"
                continue
            except Exception as e:
                yield f"Error deleting {file_name}: {str(e)}\n"
                yield f"{APP_NAME} ending early\n"
                return

        # Check if file extension is in the whitelist
        if file_extension.lower() not in file_extension_whitelist:
            yield f"Error File extension not whitelisted for {file_name}\n"
            yield f"{APP_NAME} ending early\n"
            return
            
        # Update file extensions if necessary (lowercase and conversions)
        new_extension = extension_conversions.get(file_extension.lower(), file_extension.lower())
        if file_extension != new_extension:
            new_file_name = f"{file_base_name}{new_extension}"
            new_file_path = os.path.join(os.path.dirname(file_path), f"{file_base_name}{new_extension}")
            try:
                os.rename(file_path, new_file_path)
                file_path = new_file_path
                file_items[i] = new_file_path
            except Exception as e:
                yield f"Error updating file extension for {file_name}: {str(e)}\n"
                yield f"{APP_NAME} ending early\n"
                return

        # Use regex to extract date and title
        match = re.match(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2})\.([0-9]{2})\.([0-9]{2}) - (.+?)(?: \[(.+)\])?$", file_base_name)
        if not match:
            yield f"File Name Format Error: {file_name}\n"
            yield f"{APP_NAME} ending early\n"
            return
        
        year, month, day, hour, minute, second, title,tags = match.groups()
        date = f"{year}:{month}:{day} {hour}:{minute}:{second}"

        # Validate fields
        if re.search(r"[\[\]]", title):
            yield f"File Name Validation Error: {file_name} title contains brackets\n"
            yield f"{APP_NAME} ending early\n"
            return
        if '  ' in file_name:
            yield f"File Name Validation Error: {file_name} contains consecutive spaces.\n"
            yield f"{APP_NAME} ending early\n"
            return

        # Format fields
        if tags:
            tags_list = tags.split(tag_delimiter)
        else:
            tags = ""
            tags_list = ""
        title = title.replace("_", "-")

        # Build ExifTool command
        exif_command = [
            "exiftool", 
            "-overwrite_original",
            "-P",
            
            # Date Fields
            f"-Time:all={date} +00:00", # Use UTC universally

            # Title Fields
            f"-Title={title}",
            f"-By-line=",
            f"-Caption-Abstract=",
            f"-ImageDescription={title}",
            f"-Description={title}",
            f"-ObjectName={title}",
            f"-Subtitle=",
            f"-XPComment=",
            f"-URL=",

            # Rating Fields
            # (Removes all ratings set in Windows)
            f"-Rating=",
            f"-RatingPercent=",
            f"-SharedUserRating=",

            # Author Fields
            f"-Author={family_name}",
            f"-XPAuthor={family_name}",
            f"-Creator={family_name}",
            f"-Artist={family_name}",

            # Copyright Fields
            f"-Copyright={copyright_notice}",
            f"-CopyrightNotice=",
            f"-Rights=",
            f"-UsageTerms=",
            f"-WebStatement=",
            f"-Marked=",

            # File Name Fields
            f"-RawFileName={file_name}",

            # Miscellaneous Fields               
            f"-XMP-iptcCore:CountryCode=",
            f"-XMP-iptcCore:CreatorContactInfo=",
            f"-XMP-iptcCore:CreatorCity=",
            f"-XMP-iptcCore:CreatorCountry=",
            f"-XMP-iptcCore:CreatorAddress=",
            f"-XMP-iptcCore:CreatorPostalCode=",
            f"-XMP-iptcCore:CreatorRegion=",
            f"-XMP-iptcCore:CreatorWorkEmail=",
            f"-XMP-iptcCore:CreatorWorkTelephone=",
            f"-XMP-iptcCore:CreatorWorkURL=",
               
            f"-XMP-photoshop:TextLayerName=",
            f"-XMP-photoshop:TextLayerText=",
                
            f"-DerivedFromDocumentID=",
            f"-DerivedFromOriginalDocumentID=",
            f"-OriginalDocumentID=",
            f"-DocumentID=",
            f"-Software=",
            f"-HistoryAction=",
            f"-HistoryChanged=",
            f"-HistoryInstanceID=",
            f"-HistoryParameters=",
            f"-HistorySoftwareAgent=",
            f"-HistoryWhen=",
            f"-InstanceID=",
                
            file_path
        ]
            
        # Tags Fields (fields must be cleared first)
        subprocess.run([
            "exiftool",
            "-overwrite_original",
            "-P",
                
            "-XMP:HierarchicalSubject=", 
            "-XMP:Subject=", 
            "-IPTC:Keywords=", 
            "-Microsoft:Category=",
                
            file_path
        ])
        if tags_list:
            for tag in tags_list:
                tag_pipe = tag.replace(tag_hierarchy_delimiter, "|").strip()
                tag_slash = tag.replace(tag_hierarchy_delimiter, "/").strip()

                exif_command.extend([
                    f"-XMP:HierarchicalSubject+={tag_pipe}",
                    f"-XMP:Subject+={tag_slash}",
                    f"-IPTC:Keywords+={tag_slash}",
                    f"-Microsoft:Category+={tag_slash}"
                ])
                
                
        # Geotag Fields
        if data['geotag_enabled']:
            existing_gps = has_existing_gps(file_path)
                
            if existing_gps and not data['geotag_override']:
                yield f"   Warning: Geotag data already exists for: {file_name}\n"

            elif not existing_gps or data['geotag_override']:
                if existing_gps and data['geotag_override']:
                    yield f"   Warning: Overriding existing geotag data for: {file_name}\n"
                    
                tri_coordinates = f"{data['geotag_data']['latitude']}, {data['geotag_data']['longitude']}, 0"
                location_string = f"{data['geotag_data']['city']}, {data['geotag_data']['state']}, {data['geotag_data']['country']}"
                      
                exif_command.extend([
                    f"-composite:gpslatitude={data['geotag_data']['latitude']}",
                    f"-xmp:gpslatitude={data['geotag_data']['latitude']}",
                    f"-composite:gpslongitude={data['geotag_data']['longitude']}",
                    f"-xmp:gpslongitude={data['geotag_data']['longitude']}",
                    f"-GPSAltitude=0",
                    f"-GPSAltitudeRef=0",
                        
                    f"-Keys:GPSCoordinates={tri_coordinates}",
                    f"-Userdata:GPSCoordinates={tri_coordinates}",
                    f"-Itemlist:GPSCoordinates={tri_coordinates}",
                        
                    f"-XMP:City={data['geotag_data']['city']}",
                    f"-XMP:State={data['geotag_data']['state']}",
                    f"-XMP:CountryCode={data['geotag_data']['country_code']}",
                    f"-XMP:Country={data['geotag_data']['country']}",
                    f"-XMP:CountryName={data['geotag_data']['country']}",
                       
                    f"-IPTC:City={data['geotag_data']['city']}",
                    f"-IPTC:Province-State={data['geotag_data']['state']}",
                    f"-IPTC:Country-PrimaryLocationCode={data['geotag_data']['country_code']}",
                    f"-IPTC:Country-PrimaryLocationName={data['geotag_data']['country']}",
                        
                    f"-XMP-photoshop:City={data['geotag_data']['city']}",
                    f"-XMP-photoshop:State={data['geotag_data']['state']}",
                    f"-XMP-photoshop:Country={data['geotag_data']['country']}",
                        
                    f"-XMP-iptcExt:LocationShownCity={data['geotag_data']['city']}",
                    f"-XMP-iptcExt:LocationShownProvinceState={data['geotag_data']['state']}",
                    f"-XMP-iptcExt:LocationShownCountryCode={data['geotag_data']['country_code']}",
                    f"-XMP-iptcExt:LocationShownCountryName={data['geotag_data']['country']}",
                    f"-XMP-iptcExt:LocationShownGPSLatitude={data['geotag_data']['latitude']}",
                    f"-XMP-iptcExt:LocationShownGPSLongitude={data['geotag_data']['longitude']}",
                    f"-XMP-iptcExt:LocationShownGPSAltitude=0",
                    f"-XMP-iptcExt:LocationShownGPSAltitudeRef=0",
                    f"-XMP-iptcExt:LocationShownLocationName={location_string}",
                        
                    f"-Keys:LocationName={location_string}",
                      
                    f"-GPSMapDatum=",
                    f"-GPSImgDirection=",
                    f"-GPSImgDirectionRef=",
                    f"-GPSSpeed=",
                    f"-GPSSpeedRef=",
                ])
         
        # Run ExifTool command
        result = subprocess.run(exif_command, capture_output=True, text=True)
        if result.returncode != 0:
            yield f"ExifTool processing failed for {file_name}: {result.stderr.strip()}\n"
            yield f"{APP_NAME} ending early\n"
            return       	
        yield f"File processed successfully: {file_name}\n"
           
        # Output all exif fields, if VERBOSE_LOGGING is true 
        if VERBOSE_LOGGING:
            metadata_result = subprocess.run(["exiftool", file_path], capture_output=True, text=True)
            if metadata_result.returncode == 0:
                yield f"Exif Metadata for {file_name}:\n"
                yield metadata_result.stdout + "\n"
            else:
                yield f"Error fetching metadata for {file_name}: {metadata_result.stderr.strip()}\n"
                return
        
        # Update file modified date   
        try:
            set_file_modified_date(file_path, date, TZ)
        except Exception as e:
            yield f"Error setting modified date for {file_name}: {str(e)}\n"
            yield f"{APP_NAME} ending early\n"


    # Move files if needed
    if ENABLE_MOVE_FILES and data['move_files_selected']:
        yield "All files processed successfully - now moving files\n"
        file_move_operations = []
        target_file_paths = set()
        
        for source_file_path in file_items:
            file_name = os.path.basename(source_file_path)
        
            year, month = file_name.split('-')[:2]
            month_name = calendar.month_abbr[int(month)].upper()
            formatted_month = f"{month} - {month_name}"
            target_directory = os.path.join(move_to_directory, year, formatted_month)
            target_file_path = os.path.join(target_directory, file_name)
            
            external_target_file_path = target_file_path.replace(move_to_directory, EXTERNAL_MOVE_TO_DIR)
            
            if target_file_path in target_file_paths:
                yield f"Conflict found: Multiple files have the same target path {external_target_file_path}\n"
                yield "No files will be moved.\n"
                yield f"{APP_NAME} ending early\n"
                return
        
            if os.path.exists(target_file_path):
                yield f"Conflict found: {external_target_file_path} already exists.\n"
                yield f"No files will be moved.\n"
                yield f"{APP_NAME} ending early\n"
                return
        
            target_file_paths.add(target_file_path)
            file_move_operations.append({
                "source_path": source_file_path,
                "target_path": target_file_path,
            })

        for operation in file_move_operations:
            source_file_path = operation["source_path"]
            target_file_path = operation["target_path"]
        
            # Create the target directory if it doesn't exist
            os.makedirs(os.path.dirname(target_file_path), exist_ok=True)
        
            # Move the file
            shutil.move(source_file_path, target_file_path)

    yield f"{APP_NAME} completed successfully.\n"
    
def is_valid_timezone(tz):
    try:
        ZoneInfo(tz)
        return True
    except ZoneInfoNotFoundError:
    	return False
    	  	
def has_existing_gps(file_path):
    gps_check_result = subprocess.run(
        ["exiftool", "-GPSLongitude", "-GPSLatitude", "-n", file_path], 
        capture_output=True, 
        text=True
    )
    return "GPS Longitude" in gps_check_result.stdout and "GPS Latitude" in gps_check_result.stdout
        
def set_file_modified_date(file_path, date_str, timezone):
    dt = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
    dt = dt.replace(tzinfo=ZoneInfo(timezone))
    dt_utc = dt.astimezone(ZoneInfo("UTC"))
    timestamp = dt_utc.timestamp()
    os.utime(file_path, (timestamp, timestamp))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
