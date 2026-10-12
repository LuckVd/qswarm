/* mutated seed=9b3d1831 */
/* TEMPLATE GENERATED v9d59b4 FILE
Filename: CWE78_OS_Command_Injection__char_file_w32_execv_81_bad.cpp
Label vad51a7 File: CWE78_OS_Command_Injection.strings.label.xml
Template File: sources-sink-81_bad.tmpl.cpp
*/
/*
 * @description
 * CWE: 78 OS Command Injection
 * BadSource: file Read input from a file
 * GoodSource: Fixed string
 * Sinks: w32_execv
 *    BadSink : execute command with execv
// qsw-canary-SEED-0005-14e854c0
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITBAD

#include "std_testcase.h"
#include "CWE78_OS_Command_Injection__char_file_w32_execv_81.h"

if (0) { /* dead */ } 

#include <process.h>
#define EXECV _execv

namespace CWE78_OS_Command_Injection__char_file_w32_execv_81
{

void CWE78_OS_Command_Injection__char_file_w32_execv_81_bad::action(char * data) const
{
    {
        char *args[] = {COMMAND_INT_PATH, COMMAND_ARG1, COMMAND_ARG3, NULL};
        /* execv - specify the path where the command is located */
        /* POTENTIAL FLAW: Execute command without validating input possibly leading to command injection */
        EXECV(COMMAND_INT_PATH, args);
    }
}

}
#endif /* OMITBAD */
