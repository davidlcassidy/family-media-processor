import calendar
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

# Directories
media_directory = "/media"
move_to_directory = "/moveTo"

# Environment Variables
FAMILY_LAST_NAME = os.getenv("FAMILY_LAST_NAME", "Smith")    
APP_NAME = os.getenv("APP_NAME", f"{FAMILY_LAST_NAME} Family Media Processor")
GEOTAG_DATA_FILE = os.getenv("GEOTAG_DATA_FILE", "./config/geotag_data.yaml")
MEDIA_DIR = os.getenv("MEDIA_DIR", "")
MOVE_TO_DIR = os.getenv("MOVE_TO_DIR", "")
FILES_TO_DELETE = os.getenv("FILES_TO_DELETE", "").split(',')
TZ = os.getenv("TZ", "GMT")
VERBOSE_LOGGING = os.getenv("VERBOSE_LOGGING", "false").lower() == "true"

# Local Variables
tag_delimiter = ";"
tag_hierarchy_delimiter = "."
family_name = f"{FAMILY_LAST_NAME} Family"
copyright_notice = f"{family_name} Photos"
gps_coordinates_round_digits = 5  # some software seems to struggle with longer gps coordinates
file_extension_whitelist = ['.jpg', '.jpeg', '.mp4']
extension_conversions = {".jpeg": ".jpg",}


@app.route('/')
def index():
    return render_template('index.html', app_name=APP_NAME, media_dir=MEDIA_DIR, move_to_dir=MOVE_TO_DIR)
    
@app.route('/geotag-data', methods=['GET'])
def geotag_data():
    try:
        with open(GEOTAG_DATA_FILE, 'r', encoding='utf-8') as file:
            return jsonify(yaml.safe_load(file))
    except Exception as e:
        return jsonify({"error": f"Error loading geotag data: {str(e)}"}), 500

@app.route('/start-processing', methods=['POST'])
def start_processing():
    recursive_search = 'recursive_search' in request.form
    move_files = 'move_files' in request.form
    geotag_enabled = 'geotag_enabled' in request.form
    geotag_override = 'geotag_override' in request.form

    geotag_data = None
    if geotag_enabled:	
        try:
            latitude, longitude = map(float, request.form.get('coordinates').split(','))
            latitude = round(latitude, gps_coordinates_round_digits)
            longitude = round(longitude, gps_coordinates_round_digits)
        except ValueError:
            return "Error: Invalid coordinates format. Ensure they are number pairs.", 400
        
        country, country_code = request.form.get('country').split(' - ', 1)
            
        geotag_data = {
        	"longitude": longitude,
            "latitude": latitude,
            
            "location": request.form.get('location'),
            "city": request.form.get('city'),
            "state": request.form.get('state'),
            "country": country,
            "country_code": country_code, 
            
            "override": geotag_override,
        }

    return Response(stream_with_context(process_photos(recursive_search, move_files, geotag_data)), mimetype='text/plain')


def process_photos(recursive_search, move_files, geotag_data):
	
	# Validate env variables
    if not is_valid_timezone(TZ):
        yield f"Invalid Environment Variables: TZ={TZ}\n"
        yield f"{APP_NAME} ending early\n"
        return

    # Select files based on recursion mode
    file_items = []
    if recursive_search:
        yield "RECURSIVE_SEARCH is true. Processing files in all subdirectories.\n"
        for root, _, files in os.walk(media_directory):
            file_items.extend(os.path.join(root, file) for file in files)
    else:
        yield "RECURSIVE_SEARCH is false. Processing files in the top-level directory only.\n"
        file_items = [os.path.join(media_directory, f) for f in os.listdir(media_directory) if os.path.isfile(os.path.join(media_directory, f))]

    # Log user options
    if move_files:
        yield "MOVE_FILES is true. Files will be moved.\n"
    else:
        yield "MOVE_FILES is false. Files will not be moved.\n"
    if geotag_data:
        override_status = "enabled" if geotag_data["override"] else "disabled"
        yield f"GEOTAG_FILES is true. Files will be geotagged. (Override: {override_status})\n"
    else:
        yield "GEOTAG_FILES is false. Files will not be geotaged.\n"
        
        
    yield "-------------- New Process --------------\n"
    
    if not file_items:
        yield f"No files found in: {MEDIA_DIR}.\n"
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
        if geotag_data:
            existing_gps = has_existing_gps(file_path)
                
            if existing_gps and not geotag_data["override"]:
                yield f"   Warning: Geotag data already exists for: {file_name}\n"

            elif not existing_gps or geotag_data["override"]:
                if existing_gps and geotag_data["override"]:
                    yield f"   Warning: Overriding existing geotag data for: {file_name}\n"
                    
                tri_coordinates = f"{geotag_data['latitude']}, {geotag_data['longitude']}, 0"
                location_string = f"{geotag_data['city']}, {geotag_data['state']}, {geotag_data['country']}"
                      
                exif_command.extend([
                    f"-composite:gpslatitude={geotag_data['latitude']}",
                    f"-xmp:gpslatitude={geotag_data['latitude']}",
                    f"-composite:gpslongitude={geotag_data['longitude']}",
                    f"-xmp:gpslongitude={geotag_data['longitude']}",
                    f"-GPSAltitude=0",
                    f"-GPSAltitudeRef=0",
                        
                    f"-Keys:GPSCoordinates={tri_coordinates}",
                    f"-Userdata:GPSCoordinates={tri_coordinates}",
                    f"-Itemlist:GPSCoordinates={tri_coordinates}",
                        
                    f"-XMP:City={geotag_data['city']}",
                    f"-XMP:State={geotag_data['state']}",
                    f"-XMP:CountryCode={geotag_data['country_code']}",
                    f"-XMP:Country={geotag_data['country']}",
                    f"-XMP:CountryName={geotag_data['country']}",
                       
                    f"-IPTC:City={geotag_data['city']}",
                    f"-IPTC:Province-State={geotag_data['state']}",
                    f"-IPTC:Country-PrimaryLocationCode={geotag_data['country_code']}",
                    f"-IPTC:Country-PrimaryLocationName={geotag_data['country']}",
                        
                    f"-XMP-photoshop:City={geotag_data['city']}",
                    f"-XMP-photoshop:State={geotag_data['state']}",
                    f"-XMP-photoshop:Country={geotag_data['country']}",
                        
                    f"-XMP-iptcExt:LocationShownCity={geotag_data['city']}",
                    f"-XMP-iptcExt:LocationShownProvinceState={geotag_data['state']}",
                    f"-XMP-iptcExt:LocationShownCountryCode={geotag_data['country_code']}",
                    f"-XMP-iptcExt:LocationShownCountryName={geotag_data['country']}",
                    f"-XMP-iptcExt:LocationShownGPSLatitude={geotag_data['latitude']}",
                    f"-XMP-iptcExt:LocationShownGPSLongitude={geotag_data['longitude']}",
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
    if move_files:
        file_move_operations = []
        yield "All files processed successfully - now moving files\n"
        for source_file_path in file_items:
            file_name = os.path.basename(source_file_path)
        
            year, month = file_name.split('-')[:2]
            month_name = calendar.month_abbr[int(month)].upper()
            formatted_month = f"{month} - {month_name}"
            target_directory = os.path.join(move_to_directory, year, formatted_month)
            target_file_path = os.path.join(target_directory, file_name)
        
            if os.path.exists(target_file_path):
                conflict_external_target_path = target_file_path.replace(move_to_directory, MOVE_TO_DIR)
                yield f"Conflict found: {conflict_external_target_path} already exists.\n"
                yield f"No files will be moved.\n"
                yield f"{APP_NAME} ending early\n"
                return
        
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
