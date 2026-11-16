from django.db import migrations


def install_freeze(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""
            ALTER TABLE academics_schoolclass
            ADD COLUMN campus_frozen_by_enrollment boolean NOT NULL DEFAULT false;

            UPDATE academics_schoolclass AS c
            SET campus_frozen_by_enrollment = true
            WHERE EXISTS (
                SELECT 1 FROM academics_studentenrollment AS e WHERE e.school_class_id = c.id
            );

            CREATE FUNCTION academics_guard_class_campus() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'UPDATE' AND OLD.campus_frozen_by_enrollment AND
                   (NOT NEW.campus_frozen_by_enrollment
                    OR NEW.campus_id IS DISTINCT FROM OLD.campus_id
                    OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id) THEN
                    RAISE EXCEPTION 'Class campus cannot be changed after enrollment'
                        USING ERRCODE = '23514', CONSTRAINT = 'class_campus_frozen';
                END IF;
                IF NEW.campus_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM core_campus WHERE id = NEW.campus_id AND tenant_id = NEW.tenant_id
                ) THEN
                    RAISE EXCEPTION 'Class campus belongs to another tenant or does not exist'
                        USING ERRCODE = '23514', CONSTRAINT = 'class_campus_tenant';
                END IF;
                RETURN NEW;
            END $$;

            CREATE TRIGGER academics_guard_class_campus
            BEFORE INSERT OR UPDATE OF campus_id, tenant_id, campus_frozen_by_enrollment
            ON academics_schoolclass FOR EACH ROW EXECUTE FUNCTION academics_guard_class_campus();

            CREATE FUNCTION academics_freeze_enrolled_class() RETURNS trigger LANGUAGE plpgsql AS $$
            DECLARE class_campus uuid; class_tenant uuid; student_tenant uuid; student_campus uuid;
            BEGIN
                UPDATE academics_schoolclass SET campus_frozen_by_enrollment = true
                WHERE id = NEW.school_class_id
                RETURNING campus_id, tenant_id INTO class_campus, class_tenant;
                IF NOT FOUND OR class_tenant IS DISTINCT FROM NEW.tenant_id THEN
                    RAISE EXCEPTION 'Enrollment class belongs to another tenant or does not exist'
                        USING ERRCODE = '23514', CONSTRAINT = 'enrollment_class_tenant';
                END IF;
                SELECT tenant_id, campus_id INTO student_tenant, student_campus
                FROM core_student WHERE id = NEW.student_id;
                IF NOT FOUND OR student_tenant IS DISTINCT FROM NEW.tenant_id THEN
                    RAISE EXCEPTION 'Enrollment student belongs to another tenant or does not exist'
                        USING ERRCODE = '23514', CONSTRAINT = 'enrollment_student_tenant';
                END IF;
                IF NEW.campus_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM core_campus WHERE id = NEW.campus_id AND tenant_id = NEW.tenant_id
                ) THEN
                    RAISE EXCEPTION 'Enrollment campus belongs to another tenant or does not exist'
                        USING ERRCODE = '23514', CONSTRAINT = 'enrollment_campus_tenant';
                END IF;
                IF NEW.campus_id IS NOT NULL AND class_campus IS NOT NULL
                   AND NEW.campus_id <> class_campus THEN
                    RAISE EXCEPTION 'Enrollment campus does not match class campus'
                        USING ERRCODE = '23514', CONSTRAINT = 'enrollment_class_campus';
                END IF;
                IF student_campus IS NOT NULL AND class_campus IS NOT NULL
                   AND student_campus <> class_campus THEN
                    RAISE EXCEPTION 'Student campus does not match class campus'
                        USING ERRCODE = '23514', CONSTRAINT = 'student_class_campus';
                END IF;
                RETURN NEW;
            END $$;

            CREATE TRIGGER academics_freeze_enrolled_class
            BEFORE INSERT OR UPDATE OF school_class_id, student_id, tenant_id, campus_id
            ON academics_studentenrollment FOR EACH ROW EXECUTE FUNCTION academics_freeze_enrolled_class();
        """)


def remove_freeze(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""
            DROP TRIGGER academics_freeze_enrolled_class ON academics_studentenrollment;
            DROP FUNCTION academics_freeze_enrolled_class();
            DROP TRIGGER academics_guard_class_campus ON academics_schoolclass;
            DROP FUNCTION academics_guard_class_campus();
            ALTER TABLE academics_schoolclass DROP COLUMN campus_frozen_by_enrollment;
        """)


class Migration(migrations.Migration):
    dependencies = [("academics", "0020_enrollment_provenance_revocation")]

    operations = [migrations.RunPython(install_freeze, remove_freeze)]
